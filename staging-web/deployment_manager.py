#!/usr/bin/env python3
# TODO: Switch to docker compose v2
import os
import asyncio
import subprocess
from typing import Dict, Any, List
from datetime import datetime
import json
from config import BASE_PATH, API_PATH, PIPELINE_PATH, COMPOSE_FILES, API_SERVICES, PIPELINE_SETTINGS_PATH

class DeploymentManager:
    def __init__(self):
        self.base_path = str(BASE_PATH)
        self.api_path = str(API_PATH)
        self.pipeline_path = str(PIPELINE_PATH)
        
        # Docker compose file configurations from config.py
        self.compose_files = COMPOSE_FILES
        self.api_services = API_SERVICES
    
    async def update_api_pipeline(self) -> Dict[str, Any]:
        """
        Update API and Pipeline services following the original script logic
        Returns: {"success": bool, "error": str, "docker_images": dict, "details": dict}
        """
        result = {
            "success": False,
            "error": None,
            "docker_images": {},
            "details": {
                "steps": [],
                "start_time": datetime.utcnow().isoformat()
            }
        }
        
        try:
            # Step 1: Update Pipeline
            pipeline_result = await self.update_pipeline()
            result["details"]["steps"].append({
                "name": "update_pipeline",
                "result": pipeline_result,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            if not pipeline_result["success"]:
                result["error"] = f"Pipeline update failed: {pipeline_result['error']}"
                return result
            
            # Step 2: Update API
            api_result = await self.update_api()
            result["details"]["steps"].append({
                "name": "update_api", 
                "result": api_result,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            if not api_result["success"]:
                result["error"] = f"API update failed: {api_result['error']}"
                return result
            
            # Step 3: Start Pipeline
            start_pipeline_result = await self.start_pipeline()
            result["details"]["steps"].append({
                "name": "start_pipeline",
                "result": start_pipeline_result,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            if not start_pipeline_result["success"]:
                result["error"] = f"Pipeline start failed: {start_pipeline_result['error']}"
                return result
            
            # Collect docker image information
            result["docker_images"] = {
                "api": api_result.get("images", []),
                "pipeline": pipeline_result.get("images", [])
            }
            
            result["success"] = True
            result["details"]["end_time"] = datetime.utcnow().isoformat()
            
        except Exception as e:
            result["error"] = f"Deployment exception: {str(e)}"
            result["details"]["exception"] = str(e)
        
        return result
    
    async def update_pipeline(self) -> Dict[str, Any]:
        """Update pipeline containers"""
        try:
            steps = []
            
            # Git operations
            git_result = await self.run_git_operations("pipeline", self.pipeline_path)
            steps.append({"name": "git_operations", "result": git_result})
            
            if not git_result["success"]:
                return {"success": False, "error": git_result["error"], "steps": steps}
            
            # Pull docker images
            pull_result = await self.docker_compose_pull(self.compose_files, cwd=self.pipeline_path)
            steps.append({"name": "docker_pull", "result": pull_result})
            
            if not pull_result["success"]:
                return {"success": False, "error": pull_result["error"], "steps": steps}
            
            # Stop containers
            stop_result = await self.docker_compose_down(self.compose_files, with_orphans=True, cwd=self.pipeline_path)
            steps.append({"name": "docker_stop", "result": stop_result})
            
            # If docker stop fails, try docker workaround
            if not stop_result["success"]:
                workaround_result = await self.docker_workaround()
                steps.append({"name": "docker_workaround", "result": workaround_result})
                
                # Try stopping again
                stop_retry_result = await self.docker_compose_down(self.compose_files, with_orphans=True, cwd=self.pipeline_path)
                steps.append({"name": "docker_stop_retry", "result": stop_retry_result})
                
                if not stop_retry_result["success"]:
                    return {"success": False, "error": "Failed to stop pipeline containers", "steps": steps}
            
            return {"success": True, "steps": steps, "images": pull_result.get("images", [])}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def update_api(self) -> Dict[str, Any]:
        """Update API containers"""
        try:
            steps = []
            
            # Git operations
            git_result = await self.run_git_operations("api", self.api_path)
            steps.append({"name": "git_operations", "result": git_result})
            
            if not git_result["success"]:
                return {"success": False, "error": git_result["error"], "steps": steps}
            
            # Pull docker images for API services
            pull_result = await self.docker_compose_pull(services=self.api_services, cwd=self.api_path)
            steps.append({"name": "docker_pull", "result": pull_result})
            
            if not pull_result["success"]:
                return {"success": False, "error": pull_result["error"], "steps": steps}
            
            # Stop API containers
            stop_result = await self.docker_compose_down(cwd=self.api_path)
            steps.append({"name": "docker_stop", "result": stop_result})
            
            # If stop fails, try workaround
            if not stop_result["success"]:
                workaround_result = await self.docker_workaround()
                steps.append({"name": "docker_workaround", "result": workaround_result})
                
                stop_retry_result = await self.docker_compose_down(cwd=self.api_path)
                steps.append({"name": "docker_stop_retry", "result": stop_retry_result})
                
                if not stop_retry_result["success"]:
                    return {"success": False, "error": "Failed to stop API containers", "steps": steps}
            
            # Start API containers
            start_result = await self.docker_compose_up(services=self.api_services, cwd=self.api_path)
            steps.append({"name": "docker_start", "result": start_result})
            
            if not start_result["success"]:
                return {"success": False, "error": start_result["error"], "steps": steps}
            
            return {"success": True, "steps": steps, "images": pull_result.get("images", [])}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def start_pipeline(self) -> Dict[str, Any]:
        """Start pipeline containers"""
        try:
            # Set environment variable and start pipeline
            env = os.environ.copy()
            env["SETTINGS"] = PIPELINE_SETTINGS_PATH
            
            start_result = await self.docker_compose_up(
                compose_files=self.compose_files,
                env=env,
                cwd=self.pipeline_path
            )
            
            return start_result
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def run_git_operations(self, component: str, cwd: str) -> Dict[str, Any]:
        """Run git prune, fetch, and checkout operations"""
        commands = [
            ["git", "prune"],
            ["git", "fetch", "origin"],
            ["git", "checkout", "origin/staging.kernelci.org"]
        ]
        
        results = []
        
        for cmd in commands:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )
            
            stdout, stderr = await process.communicate()
            
            result = {
                "command": " ".join(cmd),
                "return_code": process.returncode,
                "stdout": stdout.decode('utf-8', errors='ignore'),
                "stderr": stderr.decode('utf-8', errors='ignore')
            }
            results.append(result)
            
            if process.returncode != 0:
                return {
                    "success": False,
                    "error": f"Git command failed: {' '.join(cmd)}",
                    "results": results
                }
        
        return {"success": True, "results": results}
    
    async def docker_compose_pull(self, compose_files: List[str] = None, services: List[str] = None, cwd: str = None) -> Dict[str, Any]:
        """Pull docker images"""
        cmd = ["docker-compose"]
        
        if compose_files:
            cmd.extend(compose_files)
        
        cmd.append("pull")
        
        if services:
            cmd.extend(services)
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd
        )
        
        stdout, stderr = await process.communicate()
        
        # Parse pulled images from output
        images = []
        for line in stdout.decode().split('\n'):
            if 'Pulling' in line or 'Downloaded' in line:
                images.append(line.strip())
        
        return {
            "success": process.returncode == 0,
            "return_code": process.returncode,
            "stdout": stdout.decode('utf-8', errors='ignore'),
            "stderr": stderr.decode('utf-8', errors='ignore'),
            "images": images,
            "error": stderr.decode() if process.returncode != 0 else None
        }
    
    async def docker_compose_down(self, compose_files: List[str] = None, with_orphans: bool = False, cwd: str = None) -> Dict[str, Any]:
        """Stop docker containers"""
        cmd = ["docker-compose"]
        
        if compose_files:
            cmd.extend(compose_files)
        
        cmd.append("down")
        
        if with_orphans:
            cmd.append("--remove-orphans")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd
        )
        
        stdout, stderr = await process.communicate()
        
        return {
            "success": process.returncode == 0,
            "return_code": process.returncode,
            "stdout": stdout.decode('utf-8', errors='ignore'),
            "stderr": stderr.decode('utf-8', errors='ignore'),
            "error": stderr.decode() if process.returncode != 0 else None
        }
    
    async def docker_compose_up(self, compose_files: List[str] = None, services: List[str] = None, env: Dict[str, str] = None, cwd: str = None) -> Dict[str, Any]:
        """Start docker containers"""
        cmd = ["docker-compose"]
        
        if compose_files:
            cmd.extend(compose_files)
        
        cmd.extend(["up", "-d"])
        
        if services:
            cmd.extend(services)
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env or dict(os.environ),
            cwd=cwd
        )
        
        stdout, stderr = await process.communicate()
        
        return {
            "success": process.returncode == 0,
            "return_code": process.returncode,
            "stdout": stdout.decode('utf-8', errors='ignore'),
            "stderr": stderr.decode('utf-8', errors='ignore'),
            "error": stderr.decode() if process.returncode != 0 else None
        }
    
    async def docker_workaround(self) -> Dict[str, Any]:
        """Docker workaround - restart containerd and docker"""
        print("Running docker workaround: restarting containerd and docker")
        
        commands = [
            ["sudo", "systemctl", "restart", "containerd", "docker"],
            ["sleep", "5"]  # Wait for services to restart
        ]
        
        results = []
        
        for cmd in commands:
            if cmd[0] == "sleep":
                await asyncio.sleep(int(cmd[1]))
                results.append({"command": " ".join(cmd), "return_code": 0})
                continue
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            result = {
                "command": " ".join(cmd),
                "return_code": process.returncode,
                "stdout": stdout.decode('utf-8', errors='ignore'),
                "stderr": stderr.decode('utf-8', errors='ignore')
            }
            results.append(result)
            
            if process.returncode != 0:
                return {
                    "success": False,
                    "error": f"Docker workaround failed: {' '.join(cmd)}",
                    "results": results
                }
        
        # Try to run dozzle restart if script exists
        dozzle_script = os.path.expanduser("~/run-dozzle")
        if os.path.exists(dozzle_script):
            process = await asyncio.create_subprocess_exec(
                dozzle_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            results.append({
                "command": dozzle_script,
                "return_code": process.returncode,
                "stdout": stdout.decode('utf-8', errors='ignore'),
                "stderr": stderr.decode('utf-8', errors='ignore')
            })
        
        return {"success": True, "results": results}