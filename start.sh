#!/bin/sh

export PYTHONPATH=../litellm:$PYTHONPATH
#export LITELLM_DEBUG=true

/usr/bin/python3 app.py

