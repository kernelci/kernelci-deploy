#!/usr/bin/env python3
"""
KernelCI Slackbot to send/forward messages
to Slack and receive commands from Slack

Based on slackclient-2.9.4
"""

import os
import sys
import time
import json
import yaml
import slack
import threading
import argparse

# fastapi
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

# Global variables
# same directory as kci-slackbot.py
progdir = os.path.dirname(os.path.realpath(__file__))
CONFIG_FILE = os.path.join(progdir, "kci-slackbot.yaml")
CONFIG = yaml.safe_load(open(CONFIG_FILE))
HTTP_PORT = 5000

# run fastapi on port 50
app = FastAPI(
    title="KernelCI Slackbot", description="KernelCI Slackbot API",
    version="0.1.0"
)


class SlackBot(object):
    def __init__(self):
        self.slack_client = slack.WebClient(token=CONFIG["slack"]["token"])

    """
    Check if there is any message from Slack
    """

    def poll(self):
        # Any direct messages?
        response = self.slack_client.conversations_list(types="im")
        if response["ok"]:
            for channel in response["channels"]:
                if channel["is_im"]:
                    print("Found IM channel: %s" % channel["id"])
                    # print(channel)
                    self.handle_message(channel["id"])
        # Any commands from #sysadmin?
        response = self.slack_client.conversations_list(types="public_channel")
        if response["ok"]:
            for channel in response["channels"]:
                if channel["name"] == "sysadmin":
                    print("Found public channel: %s" % channel["id"])
                    self.handle_command(channel["id"])

    """
    Handle messages from Slack
    """

    def handle_message(self, channel_id):
        # For now just echo message
        response = self.slack_client.conversations_history(channel=channel_id)
        if response["ok"]:
            print("Found %d messages" % len(response["messages"]))
            for message in response["messages"]:
                print("Message: %s" % message["text"])
                if "text" in message:
                    print("Message: %s" % message["text"])
                    self.slack_client.chat_postMessage(
                        channel=channel_id, text=message["text"]
                    )


"""
API call /message?msg=HelloWorld&token=123456
"""


@app.get("/message")
def message(msg: str, token: str):
    print("Message: %s" % msg)
    if token == CONFIG["slack"]["token"]:
        slack_client = slack.WebClient(token=CONFIG["slack"]["token"])
        slack_client.chat_postMessage(channel="#sysadmin", text=msg)
        return {"message": "OK"}
    else:
        return {"message": "Invalid token"}


def uvicorn_task():
    # Start fastapi in separate thread
    while True:
        uvicorn.run(app, host="127.0.0.1", port=HTTP_PORT)


def __main__():
    args = argparse.ArgumentParser()
    args.add_argument("--message", help="Just send message and exit")
    args.add_argument("--server", help="Start server")
    args = args.parse_args()

    if args.message:
        slack_client = slack.WebClient(token=CONFIG["slack"]["token"])
        slack_client.chat_postMessage(channel="#bot", text=args.message)
        sys.exit(0)

    if args.server:
        slackbot = SlackBot()
        thread = threading.Thread(target=uvicorn_task)
        while True:
            # check if there is any message from Slack
            slackbot.poll()
            print("Sleeping for 5 second")
            time.sleep(5)


if __name__ == "__main__":
    __main__()
