
import json
import os
import time

CAPTURE_LOG_PATH=os.path.join(os.path.dirname(__file__),"DRIFT/responses.jsonl")

def capture_pair(model_name,primary_response,silent_model_name,silent_response,silent_error=None):
    record={
        "timestamp":time.time(),
        "model":model_name,
        "silent_model":silent_model_name,
        "primary":primary_response.model_dump(),
        "silent":silent_response.model_dump() if silent_response is not None else None,
        "silent_error":silent_error,
    }
    with open(CAPTURE_LOG_PATH,"a") as f:
        f.write(json.dumps(record)+"\n")


