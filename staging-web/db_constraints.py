#!/usr/bin/env python3

"""
Database constraint validation functions
"""

from datetime import datetime
from sqlalchemy.orm import Session
from models import StagingRun, StagingRunStatus
from typing import Optional

def validate_single_running_staging(db: Session, exclude_id: Optional[int] = None) -> Optional[StagingRun]:
    """
    Validate that only one staging run is in RUNNING status at a time.
    
    Args:
        db: Database session
        exclude_id: Optional staging run ID to exclude from the check (for updates)
        
    Returns:
        StagingRun if a running staging is found, None otherwise
    """
    query = db.query(StagingRun).filter(StagingRun.status == StagingRunStatus.RUNNING)
    
    if exclude_id:
        query = query.filter(StagingRun.id != exclude_id)
    
    return query.first()

def enforce_single_running_staging(db: Session, new_staging_id: int) -> bool:
    """
    Enforce that only one staging run can be in RUNNING status.
    If multiple are found, keep the oldest one and mark others as failed.
    
    Args:
        db: Database session
        new_staging_id: ID of the newly created staging run
        
    Returns:
        True if the new staging run is allowed to continue, False if it was cancelled
    """
    # Get all running staging runs
    running_stagings = db.query(StagingRun).filter(
        StagingRun.status == StagingRunStatus.RUNNING
    ).all()
    
    if len(running_stagings) <= 1:
        # No conflict
        return True
    
    # Multiple running stagings found - keep the oldest
    oldest_staging = min(running_stagings, key=lambda x: x.start_time)
    
    print(f"CONFLICT: Found {len(running_stagings)} running staging runs")
    print(f"Keeping oldest run #{oldest_staging.id}, cancelling others")
    
    # Cancel all others, including the new one if it's not the oldest
    cancelled_new = False
    for staging in running_stagings:
        if staging.id != oldest_staging.id:
            staging.status = StagingRunStatus.FAILED
            staging.end_time = datetime.utcnow()
            staging.error_message = "Cancelled due to concurrent staging run conflict"
            staging.error_step = "validation"
            
            if staging.id == new_staging_id:
                cancelled_new = True
            
            print(f"  - Cancelled run #{staging.id}")
    
    db.commit()
    return not cancelled_new

def get_running_staging_count(db: Session) -> int:
    """Get the count of currently running staging runs"""
    return db.query(StagingRun).filter(StagingRun.status == StagingRunStatus.RUNNING).count()