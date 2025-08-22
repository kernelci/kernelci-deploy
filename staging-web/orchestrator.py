#!/usr/bin/env python3

import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (
    StagingRun, StagingRunStep, StagingRunStatus, StagingStepStatus, 
    StagingStepType, User
)
from github_integration import GitHubWorkflowManager
from kernel_integration import KernelTreeManager
from deployment_manager import DeploymentManager
from self_update_manager import SelfUpdateManager
from settings import get_setting, GITHUB_TOKEN, SKIP_SELF_UPDATE
from discord_webhook import discord_webhook
from db_constraints import get_running_staging_count

class StagingOrchestrator:
    def __init__(self):
        self.github_manager = None
        self.kernel_manager = KernelTreeManager()
        self.deployment_manager = DeploymentManager()
        self.self_update_manager = SelfUpdateManager()
        
        # Initialize GitHub manager if token is available
        try:
            github_token = get_setting(GITHUB_TOKEN)
            if github_token:
                self.github_manager = GitHubWorkflowManager(github_token)
        except Exception as e:
            print(f"Warning: Could not initialize GitHub manager: {e}")
            self.github_manager = None
    
    async def process_staging_runs(self):
        """Main orchestrator function - processes all active staging runs"""
        db = SessionLocal()
        try:
            # Get all running staging runs
            active_runs = db.query(StagingRun).filter(
                StagingRun.status == StagingRunStatus.RUNNING
            ).all()
            
            # Validate: There should only be one running staging run at a time
            if len(active_runs) > 1:
                print(f"WARNING: Found {len(active_runs)} concurrent running staging runs!")
                print("This should not happen. Active runs:")
                for run in active_runs:
                    print(f"  - Run #{run.id} by {run.user.username}, started {run.start_time}")
                
                # Keep only the oldest running staging run, mark others as failed
                oldest_run = min(active_runs, key=lambda x: x.start_time)
                for run in active_runs:
                    if run.id != oldest_run.id:
                        print(f"  - Marking run #{run.id} as failed due to concurrency conflict")
                        run.status = StagingRunStatus.FAILED
                        run.end_time = datetime.utcnow()
                        run.error_message = "Cancelled due to concurrent staging run conflict"
                        run.error_step = "validation"
                
                db.commit()
                # Process only the oldest run
                active_runs = [oldest_run]
            
            for staging_run in active_runs:
                # First, recover any stuck steps from app restart
                await self.recover_stuck_steps(staging_run, db)
                await self.process_single_staging_run(staging_run, db)
                
        except Exception as e:
            print(f"Error in orchestrator: {e}")
        finally:
            db.close()
    
    async def process_single_staging_run(self, staging_run: StagingRun, db: Session):
        """Process a single staging run through its steps"""
        try:
            # Get current step or create initial steps if none exist
            if not staging_run.steps:
                await self.initialize_staging_steps(staging_run, db)
            
            # Find next step to process
            next_step = self.get_next_step_to_process(staging_run)
            
            if not next_step:
                # No more steps, check if all completed successfully
                if self.all_steps_completed_successfully(staging_run):
                    await self.complete_staging_run(staging_run, db, success=True)
                else:
                    await self.complete_staging_run(staging_run, db, success=False)
                return
            
            # Process the current step
            await self.process_step(staging_run, next_step, db)
            
        except Exception as e:
            print(f"Error processing staging run {staging_run.id}: {e}")
            await self.fail_staging_run(staging_run, db, str(e))
    
    async def initialize_staging_steps(self, staging_run: StagingRun, db: Session):
        """Initialize the steps for a staging run"""
        steps = [
            {
                "type": StagingStepType.GITHUB_WORKFLOW,
                "order": 1
            }
        ]
        
        # Check if self-update should be skipped
        skip_self_update = get_setting(SKIP_SELF_UPDATE, "true").lower() == "true"
        order_counter = 2
        
        if not skip_self_update:
            steps.append({
                "type": StagingStepType.SELF_UPDATE,
                "order": order_counter
            })
            order_counter += 1
        
        steps.extend([
            {
                "type": StagingStepType.KERNEL_TREE_UPDATE,
                "order": order_counter
            },
            {
                "type": StagingStepType.API_PIPELINE_UPDATE,
                "order": order_counter + 1
            }
        ])
        
        for step_config in steps:
            step = StagingRunStep(
                staging_run_id=staging_run.id,
                step_type=step_config["type"],
                sequence_order=step_config["order"],
                status=StagingStepStatus.PENDING
            )
            db.add(step)
        
        db.commit()
        db.refresh(staging_run)
    
    def get_next_step_to_process(self, staging_run: StagingRun) -> Optional[StagingRunStep]:
        """Get the next step that needs processing"""
        # First check if any step is currently running and needs status update
        running_step = next((s for s in staging_run.steps if s.status == StagingStepStatus.RUNNING), None)
        if running_step:
            return running_step
        
        # Check if any previous step failed - if so, skip remaining steps
        failed_step = next((s for s in staging_run.steps if s.status == StagingStepStatus.FAILED), None)
        if failed_step:
            # Mark all remaining pending steps as skipped
            pending_steps = [s for s in staging_run.steps if s.status == StagingStepStatus.PENDING]
            for step in pending_steps:
                step.status = StagingStepStatus.SKIPPED
                step.error_message = f"Skipped due to failure in step: {failed_step.step_type.value.replace('_', ' ').title()}"
            return None
        
        # Otherwise get the next pending step
        pending_steps = [s for s in staging_run.steps if s.status == StagingStepStatus.PENDING]
        if pending_steps:
            return min(pending_steps, key=lambda x: x.sequence_order)
        
        return None
    
    def all_steps_completed_successfully(self, staging_run: StagingRun) -> bool:
        """Check if all steps completed successfully (including partial success)"""
        # If any step failed, the run is not successful
        if any(step.status == StagingStepStatus.FAILED for step in staging_run.steps):
            return False
        
        # All remaining steps should be completed, partial success, or skipped (due to earlier failure)
        return all(step.status in [StagingStepStatus.COMPLETED, StagingStepStatus.PARTIAL_SUCCESS, StagingStepStatus.SKIPPED] for step in staging_run.steps)
    
    async def process_step(self, staging_run: StagingRun, step: StagingRunStep, db: Session):
        """Process a specific step based on its type and current status"""
        if step.step_type == StagingStepType.GITHUB_WORKFLOW:
            await self.process_github_workflow_step(staging_run, step, db)
        elif step.step_type == StagingStepType.SELF_UPDATE:
            await self.process_self_update_step(staging_run, step, db)
        elif step.step_type == StagingStepType.KERNEL_TREE_UPDATE:
            await self.process_kernel_tree_step(staging_run, step, db)
        elif step.step_type == StagingStepType.API_PIPELINE_UPDATE:
            await self.process_api_pipeline_step(staging_run, step, db)
    
    async def process_github_workflow_step(self, staging_run: StagingRun, step: StagingRunStep, db: Session):
        """Process GitHub workflow step"""
        # Always re-check GitHub token in case it was updated since orchestrator initialization
        try:
            github_token = get_setting(GITHUB_TOKEN)
            if github_token and github_token.strip():
                # Always reinitialize to ensure we have the latest token
                print(f"Initializing/updating GitHub manager with token during step processing")
                self.github_manager = GitHubWorkflowManager(github_token)
            else:
                print(f"GitHub token is empty or not set: '{github_token}'")
                self.github_manager = None
        except Exception as e:
            print(f"Error checking GitHub token during step processing: {e}")
            self.github_manager = None
        
        if not self.github_manager:
            step.status = StagingStepStatus.FAILED
            step.error_message = "GitHub token not configured or invalid"
            db.commit()
            return
        
        if step.status == StagingStepStatus.PENDING:
            # Start the workflow
            step.status = StagingStepStatus.RUNNING
            step.start_time = datetime.utcnow()
            staging_run.current_step = "github_workflow"
            db.commit()
            
            # Trigger workflow
            workflow_run_id = await self.github_manager.trigger_workflow()
            if workflow_run_id:
                step.github_actions_id = workflow_run_id
                step.details = json.dumps({"workflow_run_id": workflow_run_id, "status": "triggered"})
                db.commit()
            else:
                step.status = StagingStepStatus.FAILED
                step.error_message = "Failed to trigger GitHub workflow"
                db.commit()
        
        elif step.status == StagingStepStatus.RUNNING:
            # Check workflow status
            if step.github_actions_id:
                status = await self.github_manager.get_workflow_run_status(step.github_actions_id)
                step.details = json.dumps(status)
                
                if status["status"] == "completed":
                    step.end_time = datetime.utcnow()
                    
                    # Determine the actual result based on job details
                    jobs_summary = status.get("jobs_summary", {})
                    workflow_url = status.get("html_url", "")
                    
                    if status["conclusion"] == "success":
                        step.status = StagingStepStatus.COMPLETED
                    elif self._is_partial_success(status, jobs_summary):
                        # Partial success - some jobs failed but workflow didn't completely fail
                        step.status = StagingStepStatus.PARTIAL_SUCCESS
                        if workflow_url:
                            step.error_message = f"Partial success: {jobs_summary.get('success', 0)}/{jobs_summary.get('total', 0)} jobs passed. View details: {workflow_url}"
                        else:
                            step.error_message = f"Partial success: {jobs_summary.get('success', 0)}/{jobs_summary.get('total', 0)} jobs passed"
                    else:
                        # Complete failure
                        step.status = StagingStepStatus.FAILED
                        if workflow_url:
                            step.error_message = f"Workflow failed with conclusion: {status['conclusion']}. View details: {workflow_url}"
                        else:
                            step.error_message = f"Workflow failed with conclusion: {status['conclusion']}"
                
                db.commit()
    
    async def process_self_update_step(self, staging_run: StagingRun, step: StagingRunStep, db: Session):
        """Process self-update step (git pull)"""
        if step.status == StagingStepStatus.PENDING:
            step.status = StagingStepStatus.RUNNING
            step.start_time = datetime.utcnow()
            staging_run.current_step = "self_update"
            db.commit()
            
            try:
                # Update staging script
                result = await self.self_update_manager.update_staging_script()
                
                step.end_time = datetime.utcnow()
                if result["success"]:
                    step.status = StagingStepStatus.COMPLETED
                    step.details = json.dumps(result)
                    
                    # Get commit info if updated
                    if result.get("update_status") == "updated":
                        commit_info = await self.self_update_manager.get_current_commit_info()
                        if commit_info["success"]:
                            step.git_commit_sha = commit_info.get("commit_sha")
                else:
                    step.status = StagingStepStatus.FAILED
                    step.error_message = result.get("error", "Unknown error")
                
                db.commit()
                
            except Exception as e:
                step.status = StagingStepStatus.FAILED
                step.error_message = str(e)
                step.end_time = datetime.utcnow()
                db.commit()
    
    async def process_kernel_tree_step(self, staging_run: StagingRun, step: StagingRunStep, db: Session):
        """Process kernel tree update step"""
        if step.status == StagingStepStatus.PENDING:
            step.status = StagingStepStatus.RUNNING
            step.start_time = datetime.utcnow()
            staging_run.current_step = "kernel_tree_update"
            db.commit()
            
            try:
                # Determine kernel tree (rotate or use specified)
                kernel_tree = staging_run.kernel_tree or self.kernel_manager.rotate_tree()
                staging_run.kernel_tree = kernel_tree
                
                # Update kernel tree
                result = await self.kernel_manager.update_kernel_tree(kernel_tree)
                
                step.end_time = datetime.utcnow()
                if result["success"]:
                    step.status = StagingStepStatus.COMPLETED
                    step.git_commit_sha = result.get("commit_sha")
                    step.details = json.dumps(result)
                else:
                    step.status = StagingStepStatus.FAILED
                    step.error_message = result.get("error", "Unknown error")
                
                db.commit()
                
            except Exception as e:
                step.status = StagingStepStatus.FAILED
                step.error_message = str(e)
                step.end_time = datetime.utcnow()
                db.commit()
    
    async def process_api_pipeline_step(self, staging_run: StagingRun, step: StagingRunStep, db: Session):
        """Process API/pipeline deployment step"""
        if step.status == StagingStepStatus.PENDING:
            step.status = StagingStepStatus.RUNNING
            step.start_time = datetime.utcnow()
            staging_run.current_step = "api_pipeline_update"
            db.commit()
            
            try:
                result = await self.deployment_manager.update_api_pipeline()
                
                step.end_time = datetime.utcnow()
                if result["success"]:
                    step.status = StagingStepStatus.COMPLETED
                    step.docker_images = result.get("docker_images")
                    step.details = json.dumps(result)
                else:
                    step.status = StagingStepStatus.FAILED
                    step.error_message = result.get("error", "Unknown error")
                
                db.commit()
                
            except Exception as e:
                step.status = StagingStepStatus.FAILED
                step.error_message = str(e)
                step.end_time = datetime.utcnow()
                db.commit()
    
    async def complete_staging_run(self, staging_run: StagingRun, db: Session, success: bool):
        """Complete a staging run"""
        staging_run.status = StagingRunStatus.COMPLETED if success else StagingRunStatus.FAILED
        staging_run.end_time = datetime.utcnow()
        staging_run.current_step = None
        
        if not success:
            failed_step = next((s for s in staging_run.steps if s.status == StagingStepStatus.FAILED), None)
            if failed_step:
                staging_run.error_step = failed_step.step_type.value
                staging_run.error_message = failed_step.error_message
        
        db.commit()
        
        # Send Discord notification
        if discord_webhook:
            try:
                duration = None
                if staging_run.start_time and staging_run.end_time:
                    duration_seconds = (staging_run.end_time - staging_run.start_time).total_seconds()
                    duration = f"{int(duration_seconds // 60)} minutes"
                
                await discord_webhook.send_staging_complete(
                    staging_run.user.username,
                    staging_run.id,
                    "succeeded" if success else "failed",
                    duration
                )
            except Exception as e:
                print(f"Failed to send Discord notification: {e}")
    
    async def fail_staging_run(self, staging_run: StagingRun, db: Session, error: str):
        """Fail a staging run with error"""
        staging_run.status = StagingRunStatus.FAILED
        staging_run.end_time = datetime.utcnow()
        staging_run.error_message = error
        db.commit()

    async def recover_stuck_steps(self, staging_run: StagingRun, db: Session):
        """Recover steps that may be stuck due to app restart"""
        current_time = datetime.utcnow()
        
        for step in staging_run.steps:
            if step.status == StagingStepStatus.RUNNING:
                # Calculate how long the step has been running
                if step.start_time:
                    running_duration = current_time - step.start_time
                    timeout_minutes = 30  # Default timeout
                    
                    # Set different timeouts based on step type
                    if step.step_type == StagingStepType.GITHUB_WORKFLOW:
                        timeout_minutes = 60  # GitHub workflows can take longer
                    elif step.step_type == StagingStepType.API_PIPELINE_UPDATE:
                        timeout_minutes = 20  # Docker builds can take time
                    elif step.step_type == StagingStepType.KERNEL_TREE_UPDATE:
                        timeout_minutes = 15  # Git operations
                    elif step.step_type == StagingStepType.SELF_UPDATE:
                        timeout_minutes = 10  # Quick git pull
                    
                    if running_duration.total_seconds() > (timeout_minutes * 60):
                        print(f"Detected stuck step {step.step_type.value} in run #{staging_run.id}")
                        print(f"Step has been running for {running_duration.total_seconds()/60:.1f} minutes (timeout: {timeout_minutes}m)")
                        
                        # For GitHub workflow steps, check if they're actually still running
                        if step.step_type == StagingStepType.GITHUB_WORKFLOW and step.github_actions_id:
                            try:
                                if self.github_manager:
                                    status = await self.github_manager.get_workflow_run_status(step.github_actions_id)
                                    if status["status"] == "completed":
                                        print(f"GitHub workflow {step.github_actions_id} completed but wasn't processed - recovering")
                                        continue  # Let normal processing handle this
                                    elif status["status"] == "in_progress":
                                        print(f"GitHub workflow {step.github_actions_id} is still running - extending timeout")
                                        continue  # Still actually running
                            except Exception as e:
                                print(f"Error checking GitHub workflow status: {e}")
                        
                        # Mark as failed due to timeout
                        step.status = StagingStepStatus.FAILED
                        step.end_time = current_time
                        step.error_message = f"Step timed out after {running_duration.total_seconds()/60:.1f} minutes (likely due to application restart)"
                        
                        print(f"Marked step {step.step_type.value} as failed due to timeout")
        
        db.commit()
    
    async def startup_recovery(self):
        """Recovery function to run on app startup - handles steps that can't survive restart"""
        print("Running startup recovery check...")
        db = SessionLocal()
        try:
            # Find all running staging runs
            running_stagings = db.query(StagingRun).filter(
                StagingRun.status == StagingRunStatus.RUNNING
            ).all()
            
            for staging_run in running_stagings:
                print(f"Checking staging run #{staging_run.id} for steps that need startup recovery")
                recovered_any = False
                
                for step in staging_run.steps:
                    if step.status == StagingStepStatus.RUNNING:
                        # Local operations that definitely can't survive app restart
                        if step.step_type in [StagingStepType.API_PIPELINE_UPDATE, 
                                            StagingStepType.KERNEL_TREE_UPDATE, 
                                            StagingStepType.SELF_UPDATE]:
                            print(f"Recovering stuck local step: {step.step_type.value}")
                            step.status = StagingStepStatus.FAILED
                            step.end_time = datetime.utcnow()
                            step.error_message = f"Step failed due to application restart - local operations cannot survive restarts"
                            recovered_any = True
                        
                        # GitHub workflows might still be running, so we'll let the normal recovery handle them
                        elif step.step_type == StagingStepType.GITHUB_WORKFLOW:
                            print(f"GitHub workflow step found - will be checked by normal recovery process")
                
                if recovered_any:
                    db.commit()
                    print(f"Recovered {sum(1 for s in staging_run.steps if s.status == StagingStepStatus.FAILED and 'application restart' in (s.error_message or ''))} steps for staging run #{staging_run.id}")
                        
        except Exception as e:
            print(f"Error during startup recovery: {e}")
        finally:
            db.close()
    
    def _is_partial_success(self, status: Dict[str, Any], jobs_summary: Dict[str, Any]) -> bool:
        """
        Determine if workflow result should be considered a partial success
        
        Partial success criteria:
        - Workflow conclusion is 'failure' but some jobs succeeded
        - At least 50% of jobs passed
        - No critical failures (all failures are from optional/allowed-to-fail jobs)
        """
        if not jobs_summary or jobs_summary.get("total", 0) == 0:
            return False
            
        total_jobs = jobs_summary.get("total", 0)
        success_jobs = jobs_summary.get("success", 0)
        failure_jobs = jobs_summary.get("failure", 0)
        
        # If no failures, it should have been marked as success already
        if failure_jobs == 0:
            return False
            
        # If there are some successes and the success rate is >= 50%, consider it partial success
        if success_jobs > 0 and (success_jobs / total_jobs) >= 0.5:
            return True
            
        return False

# Global orchestrator instance
orchestrator = StagingOrchestrator()