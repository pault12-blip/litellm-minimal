#!/bin/sh

# Prepare checkpoint
./mk_checkpoint.py > FOCUS/checkpoint.tab

# Reconcile!
/usr/bin/python3 focus.py


