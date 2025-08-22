#!/usr/bin/env python3

import os
import json
import asyncio
from typing import List
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException, status, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import uvicorn

from config import APP_TITLE, HOST, PORT, ORCHESTRATOR_INTERVAL_SECONDS, MAX_RECENT_RUNS, ACCESS_TOKEN_EXPIRE_MINUTES

from database import SessionLocal, engine, init_db
from models import (
    User, StagingRun, StagingRunStep, StagingRunStatus, 
    StagingStepStatus, StagingStepType, UserRole
)
from auth import get_password_hash, verify_password, create_access_token, get_current_user, get_current_user_optional
from settings import get_setting, set_setting, DISCORD_WEBHOOK_URL, GITHUB_TOKEN, SKIP_SELF_UPDATE
from discord_webhook import configure_discord_webhook, discord_webhook
from db_constraints import validate_single_running_staging, enforce_single_running_staging

# Initialize database first
init_db()

# Configure Discord webhook
configure_discord_webhook(get_setting(DISCORD_WEBHOOK_URL))

# Import orchestrator after database is initialized
from orchestrator import orchestrator

# Background task to run orchestrator
async def run_orchestrator():
    while True:
        try:
            await orchestrator.process_staging_runs()
        except Exception as e:
            print(f"Orchestrator error: {e}")
        
        # Run every minute
        await asyncio.sleep(ORCHESTRATOR_INTERVAL_SECONDS)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    from orchestrator import StagingOrchestrator
    orchestrator = StagingOrchestrator()
    
    # Run startup recovery to handle steps that can't survive restart
    await orchestrator.startup_recovery()
    
    orchestrator_task = asyncio.create_task(run_orchestrator())
    yield
    # Shutdown
    orchestrator_task.cancel()
    try:
        await orchestrator_task
    except asyncio.CancelledError:
        pass

# Create app with lifespan
app = FastAPI(title=APP_TITLE, lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="templates"), name="static")

templates = Jinja2Templates(directory="templates")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_real_ip(request: Request) -> str:
    """Extract real client IP from request, handling proxy headers"""
    # Common proxy headers in order of preference
    proxy_headers = [
        "x-forwarded-for",
        "x-real-ip", 
        "x-client-ip",
        "cf-connecting-ip",  # Cloudflare
        "true-client-ip",    # Cloudflare Enterprise
        "x-forwarded",
        "forwarded-for",
        "forwarded"
    ]
    
    # Check proxy headers
    for header in proxy_headers:
        if header in request.headers:
            ip = request.headers[header]
            # X-Forwarded-For can contain multiple IPs, get the first one (original client)
            if header == "x-forwarded-for" and "," in ip:
                ip = ip.split(",")[0].strip()
            # Remove port if present
            if ":" in ip and not ip.startswith("["):  # Avoid IPv6 confusion
                ip = ip.split(":")[0]
            if ip and ip != "unknown":
                return ip
    
    # Fallback to direct connection IP
    return request.client.host if request.client else "unknown"

@app.get("/ajax.png")
async def get_ajax_icon():
    """Serve the AJAX loading icon directly"""
    import os
    file_path = os.path.join(os.path.dirname(__file__), "templates", "ajax.png")
    return FileResponse(file_path, media_type="image/png")

@app.get("/robots.txt")
async def get_robots_txt():
    """Serve robots.txt to deny all crawlers"""
    import os
    file_path = os.path.join(os.path.dirname(__file__), "robots.txt")
    return FileResponse(file_path, media_type="text/plain")

@app.get("/debug/ip")
async def debug_ip_info(request: Request, current_user: User = Depends(get_current_user_optional)):
    """Debug endpoint to show IP detection info (admin only)"""
    # Only allow for authenticated admin users
    if not current_user or current_user.role.value != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")
    
    real_ip = get_real_ip(request)
    
    return {
        "detected_real_ip": real_ip,
        "request_client_host": request.client.host if request.client else None,
        "all_headers": dict(request.headers),
        "proxy_headers": {
            header: request.headers.get(header, "not present") 
            for header in [
                "x-forwarded-for", "x-real-ip", "x-client-ip", 
                "cf-connecting-ip", "true-client-ip", "x-forwarded",
                "forwarded-for", "forwarded"
            ]
        }
    }

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, current_user: User = Depends(get_current_user_optional), db: Session = Depends(get_db)):
    # Redirect to login if not authenticated
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
        
    staging_runs = db.query(StagingRun).order_by(StagingRun.start_time.desc()).limit(MAX_RECENT_RUNS).all()
    
    # Check if there's a currently running staging cycle
    running_staging = db.query(StagingRun).filter(StagingRun.status == StagingRunStatus.RUNNING).first()
    
    # Get detailed step information for recent runs
    for run in staging_runs:
        run.step_details = {
            step.step_type.value: {
                "status": step.status.value,
                "start_time": step.start_time,
                "end_time": step.end_time,
                "error": step.error_message,
                "details": step.details
            }
            for step in run.steps
        }
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": current_user,
        "staging_runs": staging_runs,
        "running_staging": running_staging
    })

@app.get("/staging/{run_id}", response_class=HTMLResponse)
async def staging_run_detail(
    run_id: int, 
    request: Request, 
    current_user: User = Depends(get_current_user_optional), 
    db: Session = Depends(get_db)
):
    # Redirect to login if not authenticated
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    staging_run = db.query(StagingRun).filter(StagingRun.id == run_id).first()
    if not staging_run:
        raise HTTPException(status_code=404, detail="Staging run not found")
    
    # Check if there's a currently running staging cycle
    running_staging = db.query(StagingRun).filter(StagingRun.status == StagingRunStatus.RUNNING).first()
    
    # Check for error parameter
    error_message = None
    if request.query_params.get("error") == "already_running":
        error_message = "Cannot start a new staging run while this one is still running."
    
    return templates.TemplateResponse("staging_detail.html", {
        "request": request,
        "user": current_user,
        "staging_run": staging_run,
        "running_staging": running_staging,
        "error": error_message
    })

@app.get("/api/staging/{run_id}/status")
async def get_staging_run_status(
    run_id: int,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    # Return 401 if not authenticated
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    staging_run = db.query(StagingRun).filter(StagingRun.id == run_id).first()
    if not staging_run:
        raise HTTPException(status_code=404, detail="Staging run not found")
    
    steps = []
    for step in staging_run.steps:
        step_data = {
            "id": step.id,
            "type": step.step_type.value,
            "status": step.status.value,
            "start_time": step.start_time.isoformat() if step.start_time else None,
            "end_time": step.end_time.isoformat() if step.end_time else None,
            "error_message": step.error_message,
            "sequence_order": step.sequence_order
        }
        
        # Parse details if JSON
        if step.details:
            try:
                step_data["details"] = json.loads(step.details)
            except:
                step_data["details"] = step.details
        
        # Add step-specific data
        if step.github_actions_id:
            step_data["github_actions_id"] = step.github_actions_id
        if step.git_commit_sha:
            step_data["git_commit_sha"] = step.git_commit_sha
        if step.docker_images:
            step_data["docker_images"] = step.docker_images
            
        steps.append(step_data)
    
    return {
        "id": staging_run.id,
        "status": staging_run.status.value,
        "start_time": staging_run.start_time.isoformat(),
        "end_time": staging_run.end_time.isoformat() if staging_run.end_time else None,
        "current_step": staging_run.current_step,
        "kernel_tree": staging_run.kernel_tree,
        "error_message": staging_run.error_message,
        "error_step": staging_run.error_step,
        "steps": steps
    }

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    real_ip = get_real_ip(request)
    
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        print(f"Failed login attempt for user '{username}' from IP {real_ip}")
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid username or password"
        })
    
    print(f"Successful login for user '{username}' from IP {real_ip}")
    
    # Create access token for authenticated user
    access_token = create_access_token(data={"sub": user.username})
    
    # Check if default admin/admin and redirect to change password
    if username == "admin" and password == "admin":
        response = RedirectResponse(url="/profile", status_code=302)
        response.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=True)
        return response
    
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=True)
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key="access_token")
    return response

@app.post("/api/refresh-token")
async def refresh_token(current_user: User = Depends(get_current_user_optional)):
    """Refresh JWT token for authenticated user"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    # Create new access token
    access_token = create_access_token(data={"sub": current_user.username})
    
    # Create response and set cookie
    response = JSONResponse({
        "success": True,
        "message": "Token refreshed successfully",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60  # seconds
    })
    
    response.set_cookie(
        key="access_token", 
        value=f"Bearer {access_token}", 
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="strict"
    )
    
    return response

@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, current_user: User = Depends(get_current_user_optional)):
    # Redirect to login if not authenticated
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": current_user
    })

@app.post("/profile")
async def update_profile(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    # Redirect to login if not authenticated
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    if not verify_password(current_password, current_user.password_hash):
        return templates.TemplateResponse("profile.html", {
            "request": request,
            "user": current_user,
            "error": "Current password is incorrect"
        })
    
    # Get the user from the current database session
    db_user = db.query(User).filter(User.id == current_user.id).first()
    if not db_user:
        return templates.TemplateResponse("profile.html", {
            "request": request,
            "user": current_user,
            "error": "User not found in database"
        })
    
    # Update the password on the session-bound user object
    db_user.password_hash = get_password_hash(new_password)
    db.commit()
    
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": current_user,
        "success": "Password updated successfully"
    })

@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, current_user: User = Depends(get_current_user_optional), db: Session = Depends(get_db)):
    # Redirect to login if not authenticated
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    users = db.query(User).all()
    
    return templates.TemplateResponse("users.html", {
        "request": request,
        "user": current_user,
        "users": users
    })

@app.post("/users")
async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    email: str = Form(None),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    # Redirect to login if not authenticated
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    # Check if user already exists
    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        users = db.query(User).all()
        
        return templates.TemplateResponse("users.html", {
            "request": request,
            "user": current_user,
            "users": users,
            "error": "Username already exists"
        })
    
    new_user = User(
        username=username,
        password_hash=get_password_hash(password),
        role=UserRole(role),
        email=email
    )
    db.add(new_user)
    db.commit()
    
    return RedirectResponse(url="/users", status_code=302)

@app.post("/users/{user_id}/delete")
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    # Redirect to login if not authenticated
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    user_to_delete = db.query(User).filter(User.id == user_id).first()
    if not user_to_delete:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent deleting the current admin user
    if user_to_delete.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )
    
    db.delete(user_to_delete)
    db.commit()
    
    return RedirectResponse(url="/users", status_code=302)

@app.post("/users/{user_id}/change-password")
async def change_user_password(
    user_id: int,
    new_password: str = Form(...),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    # Redirect to login if not authenticated
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    # Find the user to update
    user_to_update = db.query(User).filter(User.id == user_id).first()
    if not user_to_update:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Validate password length
    if len(new_password) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 4 characters long"
        )
    
    # Update the password
    user_to_update.password_hash = get_password_hash(new_password)
    db.commit()
    
    return JSONResponse({
        "success": True,
        "message": f"Password changed successfully for user {user_to_update.username}"
    })

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, current_user: User = Depends(get_current_user_optional)):
    # Redirect to login if not authenticated
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    discord_webhook_url = get_setting(DISCORD_WEBHOOK_URL, "")
    github_token = get_setting(GITHUB_TOKEN, "")
    skip_self_update = get_setting(SKIP_SELF_UPDATE, "true").lower() == "true"
    
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": current_user,
        "discord_webhook_url": discord_webhook_url,
        "github_token": github_token,
        "skip_self_update": skip_self_update
    })

@app.post("/settings")
async def update_settings(
    request: Request,
    discord_webhook_url: str = Form(""),
    github_token: str = Form(""),
    skip_self_update: bool = Form(True),
    current_user: User = Depends(get_current_user_optional)
):
    # Redirect to login if not authenticated
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    set_setting(DISCORD_WEBHOOK_URL, discord_webhook_url)
    set_setting(GITHUB_TOKEN, github_token)
    set_setting(SKIP_SELF_UPDATE, "true" if skip_self_update else "false")
    configure_discord_webhook(discord_webhook_url)
    
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": current_user,
        "discord_webhook_url": discord_webhook_url,
        "github_token": github_token,
        "skip_self_update": skip_self_update,
        "success": "Settings updated successfully"
    })

@app.post("/staging/trigger")
async def trigger_staging(
    request: Request,
    kernel_tree: str = Form("auto"),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    real_ip = get_real_ip(request)
    
    # Redirect to login if not authenticated
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    if current_user.role not in [UserRole.ADMIN, UserRole.MAINTAINER]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    
    # Check if there's already a running staging cycle (first check)
    running_staging = validate_single_running_staging(db)
    if running_staging:
        # Return to the running staging detail page with an error message
        return RedirectResponse(url=f"/staging/{running_staging.id}?error=already_running", status_code=302)
    
    try:
        print(f"User '{current_user.username}' triggered staging run (kernel_tree={kernel_tree}) from IP {real_ip}")
        
        # Create new staging run record
        staging_run = StagingRun(
            user_id=current_user.id,
            status=StagingRunStatus.RUNNING,
            initiated_via="manual",
            kernel_tree=kernel_tree if kernel_tree != "auto" else None
        )
        db.add(staging_run)
        db.flush()  # Get the ID without committing
        
        # Enforce single running staging constraint
        if not enforce_single_running_staging(db, staging_run.id):
            # This staging run was cancelled due to conflict
            db.rollback()
            # Find the surviving staging run
            surviving_staging = validate_single_running_staging(db)
            if surviving_staging:
                return RedirectResponse(url=f"/staging/{surviving_staging.id}?error=already_running", status_code=302)
            else:
                return RedirectResponse(url="/?error=concurrency_conflict", status_code=302)
        
        # Safe to commit - we're the only running staging
        db.commit()
        db.refresh(staging_run)
        
    except Exception as e:
        # Database error - rollback and show error
        db.rollback()
        print(f"Error creating staging run: {e}")
        return RedirectResponse(url="/?error=database_error", status_code=302)
    
    # Send Discord notification
    if discord_webhook:
        try:
            await discord_webhook.send_staging_start(current_user.username, staging_run.id)
        except Exception as e:
            print(f"Failed to send Discord notification: {e}")
    
    return RedirectResponse(url=f"/staging/{staging_run.id}", status_code=302)

@app.get("/api/staging/status")
async def get_staging_status(current_user: User = Depends(get_current_user_optional), db: Session = Depends(get_db)):
    """Get overall staging system status"""
    # Return 401 if not authenticated
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    from db_constraints import get_running_staging_count
    from datetime import datetime
    
    running_staging = validate_single_running_staging(db)
    running_count = get_running_staging_count(db)
    
    return {
        "has_running_staging": running_staging is not None,
        "running_staging_id": running_staging.id if running_staging else None,
        "running_count": running_count,
        "system_healthy": running_count <= 1,
        "timestamp": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)