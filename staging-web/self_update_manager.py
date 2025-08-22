#!/usr/bin/env python3

import os
import asyncio
import subprocess
from typing import Dict, Any
from datetime import datetime
from config import BASE_PATH

class SelfUpdateManager:
    def __init__(self):
        self.base_path = str(BASE_PATH)
    
    async def update_staging_script(self) -> Dict[str, Any]:
        """
        Update the staging script itself (equivalent to cmd_pull in original script)
        Returns: {"success": bool, "error": str, "details": dict}
        """
        try:
            start_time = datetime.utcnow()
            
            # Run git pull --ff-only
            process = await asyncio.create_subprocess_exec(
                "git", "pull", "--ff-only",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.base_path
            )
            
            stdout, stderr = await process.communicate()
            end_time = datetime.utcnow()
            
            duration = (end_time - start_time).total_seconds()
            
            stdout_str = stdout.decode('utf-8', errors='ignore')
            stderr_str = stderr.decode('utf-8', errors='ignore')
            
            if process.returncode == 0:
                # Check if there were any updates
                if "Already up to date" in stdout_str or "Already up-to-date" in stdout_str:
                    update_status = "no_updates"
                elif "Fast-forward" in stdout_str or "files changed" in stdout_str:
                    update_status = "updated"
                else:
                    update_status = "unknown"
                
                return {
                    "success": True,
                    "update_status": update_status,
                    "duration_seconds": duration,
                    "details": {
                        "stdout": stdout_str,
                        "stderr": stderr_str,
                        "start_time": start_time.isoformat(),
                        "end_time": end_time.isoformat(),
                        "return_code": process.returncode
                    }
                }
            else:
                return {
                    "success": False,
                    "error": f"Git pull failed with return code {process.returncode}",
                    "duration_seconds": duration,
                    "details": {
                        "stdout": stdout_str,
                        "stderr": stderr_str,
                        "return_code": process.returncode
                    }
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": f"Exception during self-update: {str(e)}",
                "details": {
                    "exception": str(e)
                }
            }
    
    async def get_current_commit_info(self) -> Dict[str, Any]:
        """Get current git commit information"""
        try:
            # Get current commit info
            process = await asyncio.create_subprocess_exec(
                "git", "log", "-1", "--format=%H|%s|%cd",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.base_path
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                commit_info = stdout.decode().strip().split('|')
                return {
                    "success": True,
                    "commit_sha": commit_info[0] if len(commit_info) > 0 else None,
                    "commit_subject": commit_info[1] if len(commit_info) > 1 else None,
                    "commit_date": commit_info[2] if len(commit_info) > 2 else None
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to get git commit information"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def check_for_updates(self) -> Dict[str, Any]:
        """Check if updates are available without pulling"""
        try:
            # Fetch remote changes
            fetch_process = await asyncio.create_subprocess_exec(
                "git", "fetch", "origin",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.base_path
            )
            
            await fetch_process.communicate()
            
            if fetch_process.returncode != 0:
                return {
                    "success": False,
                    "error": "Failed to fetch from remote"
                }
            
            # Check if local is behind remote
            status_process = await asyncio.create_subprocess_exec(
                "git", "status", "-uno", "--porcelain=v1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.base_path
            )
            
            stdout, stderr = await status_process.communicate()
            
            if status_process.returncode == 0:
                status_output = stdout.decode().strip()
                has_updates = "behind" in status_output or "diverged" in status_output
                
                return {
                    "success": True,
                    "has_updates": has_updates,
                    "status": status_output
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to check git status"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }