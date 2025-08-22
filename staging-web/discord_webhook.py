#!/usr/bin/env python3

import httpx
import json
from typing import Optional
from datetime import datetime

class DiscordWebhook:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    async def send_staging_start(self, user: str, staging_run_id: int):
        """Send notification when staging run starts"""
        embed = {
            "title": "🚀 Staging Cycle Started",
            "description": f"Staging run #{staging_run_id} has been initiated by {user}",
            "color": 0x3498db,  # Blue
            "timestamp": datetime.utcnow().isoformat(),
            "fields": [
                {
                    "name": "Initiated by",
                    "value": user,
                    "inline": True
                },
                {
                    "name": "Run ID",
                    "value": f"#{staging_run_id}",
                    "inline": True
                }
            ]
        }
        
        payload = {
            "embeds": [embed],
            "username": "KernelCI Staging Bot"
        }
        
        await self._send_webhook(payload)
    
    async def send_staging_complete(self, user: str, staging_run_id: int, status: str, duration: Optional[str] = None):
        """Send notification when staging run completes"""
        if status == "succeeded":
            color = 0x2ecc71  # Green
            emoji = "✅"
            title = "Staging Cycle Completed Successfully"
        else:
            color = 0xe74c3c  # Red
            emoji = "❌"
            title = "Staging Cycle Failed"
        
        fields = [
            {
                "name": "Initiated by",
                "value": user,
                "inline": True
            },
            {
                "name": "Run ID",
                "value": f"#{staging_run_id}",
                "inline": True
            },
            {
                "name": "Status",
                "value": status.title(),
                "inline": True
            }
        ]
        
        if duration:
            fields.append({
                "name": "Duration",
                "value": duration,
                "inline": True
            })
        
        embed = {
            "title": f"{emoji} {title}",
            "description": f"Staging run #{staging_run_id} has {status}",
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
            "fields": fields
        }
        
        payload = {
            "embeds": [embed],
            "username": "KernelCI Staging Bot"
        }
        
        await self._send_webhook(payload)
    
    async def _send_webhook(self, payload: dict):
        """Send webhook to Discord"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                response.raise_for_status()
        except Exception as e:
            print(f"Failed to send Discord webhook: {e}")

# Global instance - will be configured from settings
discord_webhook: Optional[DiscordWebhook] = None

def configure_discord_webhook(webhook_url: str):
    """Configure the global Discord webhook instance"""
    global discord_webhook
    if webhook_url:
        discord_webhook = DiscordWebhook(webhook_url)