#!/usr/bin/env python3

import os
import asyncio
import subprocess
from typing import Dict, Any, Optional
from datetime import datetime
import json
from config import BASE_PATH, KERNEL_SCRIPT_PATH, TREE_FILE_PATH, KERNEL_TREES, SSH_KEY_PATH

class KernelTreeManager:
    def __init__(self):
        self.base_path = str(BASE_PATH)
        self.kernel_script_path = str(KERNEL_SCRIPT_PATH)
        self.tree_file = str(TREE_FILE_PATH)
        
        # Kernel tree configurations from config.py
        self.tree_configs = KERNEL_TREES
    
    def rotate_tree(self) -> str:
        """Rotate through kernel trees: next -> mainline -> stable -> next"""
        try:
            if os.path.exists(self.tree_file):
                with open(self.tree_file, 'r') as f:
                    last_tree = f.read().strip()
            else:
                last_tree = ""
            
            rotation = {
                "next": "mainline",
                "mainline": "stable", 
                "stable": "next",
                "": "next"  # Default if file empty/missing
            }
            
            next_tree = rotation.get(last_tree, "next")
            
            # Save the selected tree
            with open(self.tree_file, 'w') as f:
                f.write(next_tree)
                
            return next_tree
            
        except Exception as e:
            print(f"Error rotating tree: {e}")
            return "next"  # Default fallback
    
    async def update_kernel_tree(self, tree: str) -> Dict[str, Any]:
        """
        Update kernel tree using the integrated kernel.py logic
        Returns: {"success": bool, "commit_sha": str, "error": str, "details": dict}
        """
        if tree not in self.tree_configs:
            return {
                "success": False,
                "error": f"Unknown tree: {tree}",
                "details": {}
            }
        
        config = self.tree_configs[tree]
        
        try:
            # Build kernel.py command
            # maybe we should implement all that here?
            # TODO
            cmd = [
                "python3", "./kernel.py",
                "--push",
                f"--ssh-key={SSH_KEY_PATH}",
                f"--from-url={config['url']}",
                f"--from-branch={config['branch']}", 
                f"--branch={config['staging_branch']}",
                f"--tag-prefix={config['tag_prefix']}"
            ]
            
            # Execute kernel.py command
            start_time = datetime.utcnow()
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.base_path
            )
            
            stdout, stderr = await process.communicate()
            end_time = datetime.utcnow()
            
            duration = (end_time - start_time).total_seconds()
            
            # Parse output to get commit SHA
            commit_sha = None
            stdout_str = stdout.decode('utf-8', errors='ignore')
            stderr_str = stderr.decode('utf-8', errors='ignore')
            
            # Try to extract commit SHA from output
            for line in stdout_str.split('\n'):
                if 'commit' in line.lower() and len(line.split()) > 1:
                    potential_sha = line.split()[-1]
                    if len(potential_sha) >= 7 and all(c in '0123456789abcdef' for c in potential_sha[:7]):
                        commit_sha = potential_sha
                        break
            
            if process.returncode == 0:
                return {
                    "success": True,
                    "commit_sha": commit_sha,
                    "tree": tree,
                    "duration_seconds": duration,
                    "details": {
                        "stdout": stdout_str,
                        "stderr": stderr_str,
                        "config": config,
                        "start_time": start_time.isoformat(),
                        "end_time": end_time.isoformat()
                    }
                }
            else:
                return {
                    "success": False,
                    "error": f"kernel.py failed with return code {process.returncode}",
                    "tree": tree,
                    "duration_seconds": duration,
                    "details": {
                        "stdout": stdout_str,
                        "stderr": stderr_str,
                        "return_code": process.returncode,
                        "config": config
                    }
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": f"Exception during kernel tree update: {str(e)}",
                "tree": tree,
                "details": {
                    "exception": str(e),
                    "config": config
                }
            }
    
    async def get_kernel_tree_status(self, tree: str) -> Dict[str, Any]:
        """Get status information about a kernel tree"""
        if tree not in self.tree_configs:
            return {"error": f"Unknown tree: {tree}"}
        
        config = self.tree_configs[tree]
        checkout_path = os.path.join(self.base_path, "checkout", f"linux-{tree}")
        
        try:
            if not os.path.exists(checkout_path):
                return {
                    "exists": False,
                    "tree": tree,
                    "config": config
                }
            
            # Get git information
            # Get latest commit info
            process = await asyncio.create_subprocess_exec(
                "git", "log", "-1", "--format=%H|%s|%cd",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=checkout_path
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                commit_info = stdout.decode().strip().split('|')
                return {
                    "exists": True,
                    "tree": tree,
                    "latest_commit": {
                        "sha": commit_info[0] if len(commit_info) > 0 else None,
                        "subject": commit_info[1] if len(commit_info) > 1 else None,
                        "date": commit_info[2] if len(commit_info) > 2 else None
                    },
                    "config": config
                }
            else:
                return {
                    "exists": True,
                    "tree": tree,
                    "error": "Failed to get git information",
                    "config": config
                }
                
        except Exception as e:
            return {
                "exists": os.path.exists(checkout_path),
                "tree": tree,
                "error": str(e),
                "config": config
            }