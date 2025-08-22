from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

Base = declarative_base()

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MAINTAINER = "maintainer"
    VIEWER = "viewer"

class StagingStepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running" 
    COMPLETED = "completed"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    SKIPPED = "skipped"

class StagingStepType(str, enum.Enum):
    GITHUB_WORKFLOW = "github_workflow"
    SELF_UPDATE = "self_update"
    KERNEL_TREE_UPDATE = "kernel_tree_update"  
    API_PIPELINE_UPDATE = "api_pipeline_update"
    MONITORING_SETUP = "monitoring_setup"

class StagingRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed" 
    FAILED = "failed"
    CANCELLED = "cancelled"

class InitiatedVia(str, enum.Enum):
    MANUAL = "manual"
    CRON = "cron"

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.VIEWER)
    email = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    staging_runs = relationship("StagingRun", back_populates="user")

class StagingRun(Base):
    __tablename__ = "staging_runs"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    status = Column(Enum(StagingRunStatus), nullable=False, default=StagingRunStatus.PENDING)
    initiated_via = Column(Enum(InitiatedVia), nullable=False, default=InitiatedVia.MANUAL)
    
    # Kernel tree selection (next, mainline, stable)
    kernel_tree = Column(String, nullable=True)
    
    # Overall progress tracking
    current_step = Column(String, nullable=True)
    
    # Error details if failed
    error_message = Column(Text, nullable=True)
    error_step = Column(String, nullable=True)
    
    # Additional metadata
    run_metadata = Column(JSON, nullable=True)
    
    user = relationship("User", back_populates="staging_runs")
    steps = relationship("StagingRunStep", back_populates="staging_run", cascade="all, delete-orphan")

class StagingRunStep(Base):
    __tablename__ = "staging_run_steps"
    
    id = Column(Integer, primary_key=True, index=True)
    staging_run_id = Column(Integer, ForeignKey("staging_runs.id"), nullable=False)
    step_type = Column(Enum(StagingStepType), nullable=False)
    status = Column(Enum(StagingStepStatus), nullable=False, default=StagingStepStatus.PENDING)
    
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    
    # Step-specific data
    github_actions_id = Column(String, nullable=True)  # For GitHub workflow step
    git_commit_sha = Column(String, nullable=True)     # For kernel tree update
    docker_images = Column(JSON, nullable=True)        # For API/pipeline update
    
    # Progress and error tracking
    details = Column(Text, nullable=True)  # JSON or text details about step progress
    error_message = Column(Text, nullable=True)
    
    # Order of execution
    sequence_order = Column(Integer, nullable=False, default=0)
    
    staging_run = relationship("StagingRun", back_populates="steps")

class Settings(Base):
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    value = Column(String, nullable=True)