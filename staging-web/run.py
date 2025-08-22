#!/usr/bin/env python3

import subprocess
import sys
import os
from config import APP_TITLE, APP_VERSION, PORT

def setup_venv():
    """Set up virtual environment if it doesn't exist"""
    venv_path = ".venv"
    
    if not os.path.exists(venv_path):
        print("Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", venv_path], check=True)
    
    # Install requirements
    pip_path = os.path.join(venv_path, "bin", "pip")
    if os.name == "nt":  # Windows
        pip_path = os.path.join(venv_path, "Scripts", "pip.exe")
    
    print("Installing requirements...")
    subprocess.run([pip_path, "install", "-r", "requirements.txt"], check=True)

def run_app():
    """Run the application with virtual environment"""
    venv_path = ".venv"
    python_path = os.path.join(venv_path, "bin", "python")
    if os.name == "nt":  # Windows
        python_path = os.path.join(venv_path, "Scripts", "python.exe")
    
    print(f"Starting {APP_TITLE} v{APP_VERSION}...")
    print(f"Open: http://staging.kernelci.org")
    print("Login: admin/admin (change password on first login)")
    print("")
    
    os.execv(python_path, [python_path, "main.py"])

if __name__ == "__main__":
    setup_venv()
    run_app()