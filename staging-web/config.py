#!/usr/bin/env python3

import os
import toml
from pathlib import Path


def load_config():
    config_file = Path(__file__).parent / "config" / "staging.toml"
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_file}")

    with open(config_file, "r") as f:
        return toml.load(f)


config = load_config()

# Application Configuration
APP_TITLE = config["application"]["title"]
APP_VERSION = config["application"]["version"]
HOST = config["application"]["host"]
PORT = config["application"]["port"]

# Database Configuration
DATABASE_URL = config["database"]["url"]

# Authentication Configuration
SECRET_KEY = config["authentication"]["secret_key"]
ALGORITHM = config["authentication"]["algorithm"]
ACCESS_TOKEN_EXPIRE_MINUTES = config["authentication"]["access_token_expire_minutes"]

# Paths Configuration
BASE_PATH = Path(config["paths"]["base_path"])
STAGING_WEB_PATH = Path(config["paths"]["staging_web_path"])
KERNEL_SCRIPT_PATH = Path(config["paths"]["kernel_script_path"])
TREE_FILE_PATH = Path(config["paths"]["tree_file_path"])

# KernelCI Deployment Paths
API_PATH = Path(config["paths"]["api_path"])
PIPELINE_PATH = Path(config["paths"]["pipeline_path"])

# SSH Key Configuration
SSH_KEY_PATH = Path(config["paths"]["ssh_key_path"])

# Pipeline Environment Configuration
PIPELINE_SETTINGS_PATH = config["paths"]["pipeline_settings_path"]

# Docker Compose Configuration
COMPOSE_FILES = config["docker"]["compose_files"]
API_SERVICES = config["docker"]["api_services"]

# GitHub Configuration
GITHUB_REPO = config["github"]["repo"]
GITHUB_WORKFLOW = config["github"]["workflow"]
GITHUB_REF = config["github"]["ref"]

# Kernel Tree Configuration
KERNEL_TREES = config["kernel_trees"]

# Orchestrator Configuration
ORCHESTRATOR_INTERVAL_SECONDS = config["orchestrator"]["interval_seconds"]
WORKFLOW_TIMEOUT_MINUTES = config["orchestrator"]["workflow_timeout_minutes"]
WORKFLOW_CHECK_INTERVAL_SECONDS = config["orchestrator"][
    "workflow_check_interval_seconds"
]

# UI Configuration
AUTO_REFRESH_INTERVAL_SECONDS = config["ui"]["auto_refresh_interval_seconds"]
AJAX_UPDATE_INTERVAL_SECONDS = config["ui"]["ajax_update_interval_seconds"]
MAX_RECENT_RUNS = config["ui"]["max_recent_runs"]

# Default User Configuration
DEFAULT_ADMIN_USERNAME = config["default_admin"]["username"]
DEFAULT_ADMIN_PASSWORD = config["default_admin"]["password"]
DEFAULT_ADMIN_EMAIL = config["default_admin"]["email"]

# Settings Keys
SETTINGS_KEYS = {
    "GITHUB_TOKEN": config["settings_keys"]["github_token"],
    "DISCORD_WEBHOOK_URL": config["settings_keys"]["discord_webhook_url"],
    "SKIP_SELF_UPDATE": config["settings_keys"]["skip_self_update"],
}
