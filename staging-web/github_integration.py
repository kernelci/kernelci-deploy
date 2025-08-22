#!/usr/bin/env python3

import httpx
import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone
import asyncio
from config import GITHUB_REPO, GITHUB_WORKFLOW, GITHUB_REF, WORKFLOW_TIMEOUT_MINUTES, WORKFLOW_CHECK_INTERVAL_SECONDS

class GitHubWorkflowManager:
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://api.github.com"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
    
    async def trigger_workflow(self, repo: str = GITHUB_REPO, workflow: str = GITHUB_WORKFLOW, ref: str = GITHUB_REF) -> Optional[str]:
        """
        Trigger GitHub workflow and return the workflow run ID
        """
        # First check for existing running workflows
        existing_runs = await self.get_running_workflows(repo, workflow)
        if existing_runs:
            print(f"Found {len(existing_runs)} existing running workflows. Cannot trigger new workflow.")
            for run in existing_runs:
                print(f"  - Running workflow ID: {run['id']}, started: {run['created_at']}")
            return None
        
        # Record timestamp before triggering (timezone-aware)
        trigger_time = datetime.now(timezone.utc)
        
        url = f"{self.base_url}/repos/{repo}/actions/workflows/{workflow}/dispatches"
        payload = {
            "ref": ref,
            "inputs": {}
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=self.headers, json=payload)
                
                # Check for specific HTTP errors
                if response.status_code == 401:
                    print(f"Failed to trigger workflow: Authentication failed - invalid or expired GitHub token")
                    return None
                elif response.status_code == 403:
                    print(f"Failed to trigger workflow: Forbidden - insufficient permissions to trigger workflow")
                    return None
                elif response.status_code == 404:
                    print(f"Failed to trigger workflow: Workflow '{workflow}' not found in repository '{repo}'")
                    return None
                elif response.status_code == 422:
                    print(f"Failed to trigger workflow: Invalid request - check workflow configuration or ref '{ref}'")
                    return None
                
                response.raise_for_status()
                
                # GitHub doesn't return the run ID directly, we need to find it
                # Wait a moment and then get the workflow run we just triggered
                await asyncio.sleep(3)  # Increased wait time
                return await self.get_triggered_workflow_run_id(repo, workflow, trigger_time)
                
        except httpx.HTTPStatusError as e:
            print(f"Failed to trigger workflow: HTTP {e.response.status_code} - {e.response.text}")
            return None
        except httpx.RequestError as e:
            print(f"Failed to trigger workflow: Network error - {e}")
            return None
        except Exception as e:
            print(f"Failed to trigger workflow: Unexpected error - {e}")
            return None
    
    async def get_latest_workflow_run_id(self, repo: str = GITHUB_REPO, workflow: str = GITHUB_WORKFLOW) -> Optional[str]:
        """
        Get the latest workflow run ID for a specific workflow
        """
        url = f"{self.base_url}/repos/{repo}/actions/workflows/{workflow}/runs"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                
                if data.get("workflow_runs") and len(data["workflow_runs"]) > 0:
                    return str(data["workflow_runs"][0]["id"])
                
        except Exception as e:
            print(f"Failed to get latest workflow run: {e}")
            
        return None
    
    async def get_running_workflows(self, repo: str = GITHUB_REPO, workflow: str = GITHUB_WORKFLOW) -> List[Dict[str, Any]]:
        """
        Get all currently running workflow runs for a specific workflow
        """
        url = f"{self.base_url}/repos/{repo}/actions/workflows/{workflow}/runs"
        params = {
            "status": "in_progress",
            "per_page": 10
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                
                running_workflows = []
                if data.get("workflow_runs"):
                    for run in data["workflow_runs"]:
                        if run.get("status") in ["queued", "in_progress"]:
                            running_workflows.append({
                                "id": run.get("id"),
                                "status": run.get("status"),
                                "created_at": run.get("created_at"),
                                "run_number": run.get("run_number"),
                                "html_url": run.get("html_url")
                            })
                
                return running_workflows
                
        except Exception as e:
            print(f"Failed to get running workflows: {e}")
            return []
    
    async def get_triggered_workflow_run_id(self, repo: str = GITHUB_REPO, workflow: str = GITHUB_WORKFLOW, trigger_time: datetime = None) -> Optional[str]:
        """
        Get the workflow run ID that was just triggered, with safety checks
        """
        url = f"{self.base_url}/repos/{repo}/actions/workflows/{workflow}/runs"
        params = {"per_page": 5}  # Only get recent runs
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                
                if not data.get("workflow_runs"):
                    return None
                
                # Look for a workflow run that was created after our trigger time
                for run in data["workflow_runs"]:
                    run_created_at = datetime.fromisoformat(run.get("created_at", "").replace("Z", "+00:00"))
                    
                    # Safety checks:
                    # 1. Must be created after we triggered (with 1 minute buffer for clock skew)
                    # 2. Must be in queued or in_progress status (not completed from before)
                    # 3. Must be within 5 minutes of trigger time (prevent picking up unrelated runs)
                    if trigger_time:
                        time_buffer = timedelta(minutes=1)
                        max_age = timedelta(minutes=5)
                        
                        if (run_created_at >= (trigger_time - time_buffer) and 
                            run_created_at <= (trigger_time + max_age) and
                            run.get("status") in ["queued", "in_progress", "completed"]):
                            
                            print(f"Found triggered workflow run: {run.get('id')}, created: {run_created_at}, status: {run.get('status')}")
                            return str(run.get("id"))
                
                # Fallback: if no time-based match, get the latest queued/in_progress run
                for run in data["workflow_runs"]:
                    if run.get("status") in ["queued", "in_progress"]:
                        print(f"Fallback: Using latest active workflow run: {run.get('id')}")
                        return str(run.get("id"))
                        
                print("No suitable workflow run found after trigger")
                return None
                
        except Exception as e:
            print(f"Failed to get triggered workflow run: {e}")
            return None
    
    async def get_workflow_run_status(self, run_id: str, repo: str = GITHUB_REPO) -> Dict[str, Any]:
        """
        Get the status of a specific workflow run
        Returns: {
            "status": "queued|in_progress|completed",
            "conclusion": "success|failure|cancelled|skipped|timed_out|action_required|neutral",
            "created_at": "2023-...",
            "updated_at": "2023-...",
            "html_url": "https://github.com/..."
        }
        """
        url = f"{self.base_url}/repos/{repo}/actions/runs/{run_id}"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                
                # Get job details if workflow is completed
                jobs_info = None
                if data.get("status") == "completed":
                    jobs_info = await self._get_workflow_jobs_summary(run_id, repo)
                
                return {
                    "status": data.get("status"),
                    "conclusion": data.get("conclusion"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "html_url": data.get("html_url"),
                    "run_number": data.get("run_number"),
                    "workflow_id": data.get("workflow_id"),
                    "jobs_summary": jobs_info
                }
                
        except Exception as e:
            print(f"Failed to get workflow run status: {e}")
            return {
                "status": "error",
                "conclusion": "failure",
                "error": str(e)
            }
    
    async def wait_for_workflow_completion(self, run_id: str, repo: str = GITHUB_REPO, 
                                         timeout_minutes: int = WORKFLOW_TIMEOUT_MINUTES, check_interval: int = WORKFLOW_CHECK_INTERVAL_SECONDS) -> Dict[str, Any]:
        """
        Wait for workflow to complete and return final status
        """
        timeout_seconds = timeout_minutes * 60
        elapsed = 0
        
        while elapsed < timeout_seconds:
            status = await self.get_workflow_run_status(run_id, repo)
            
            if status["status"] == "completed":
                return status
            elif status["status"] == "error":
                return status
                
            await asyncio.sleep(check_interval)
            elapsed += check_interval
            
        # Timeout reached
        return {
            "status": "timeout",
            "conclusion": "timed_out",
            "error": f"Workflow did not complete within {timeout_minutes} minutes"
        }
    
    async def _get_workflow_jobs_summary(self, run_id: str, repo: str = GITHUB_REPO) -> Dict[str, Any]:
        """
        Get summary of workflow jobs to determine if it's a partial success
        """
        url = f"{self.base_url}/repos/{repo}/actions/runs/{run_id}/jobs"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                
                if not data.get("jobs"):
                    return {"total": 0, "success": 0, "failure": 0, "cancelled": 0, "skipped": 0}
                
                jobs = data["jobs"]
                summary = {
                    "total": len(jobs),
                    "success": 0,
                    "failure": 0,
                    "cancelled": 0,
                    "skipped": 0,
                    "jobs": []
                }
                
                for job in jobs:
                    conclusion = job.get("conclusion", "unknown")
                    summary["jobs"].append({
                        "name": job.get("name", "Unknown"),
                        "conclusion": conclusion,
                        "html_url": job.get("html_url")
                    })
                    
                    if conclusion == "success":
                        summary["success"] += 1
                    elif conclusion == "failure":
                        summary["failure"] += 1
                    elif conclusion == "cancelled":
                        summary["cancelled"] += 1
                    elif conclusion == "skipped":
                        summary["skipped"] += 1
                
                return summary
                
        except Exception as e:
            print(f"Failed to get workflow jobs: {e}")
            return {"total": 0, "success": 0, "failure": 0, "cancelled": 0, "skipped": 0}