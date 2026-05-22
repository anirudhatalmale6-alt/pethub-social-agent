#!/bin/bash
cd /var/lib/freelancer/projects/40416335/social-agent
source venv/bin/activate
exec python -m uvicorn main:app --host 0.0.0.0 --port 8103
