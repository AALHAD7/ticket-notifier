#!/bin/bash
cd "/Users/aalhad/ticket-notifier" || exit 1
/opt/anaconda3/bin/python3 "Google Login.py"
/opt/anaconda3/bin/python3 ticket_notifier.py
