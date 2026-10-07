"""One set of deployment limits, also returned to the browser."""
import os

def limit(name, default, maximum):
    return max(1,min(int(os.getenv('WEB_'+name,str(default))),maximum))

LIMITS = {
    'message_chars': limit('MESSAGE_CHARS',16000,64000),
    'memory_chars': limit('MEMORY_CHARS',12000,32000),
    'sessions': limit('SESSIONS',100,1000),
    'session_messages': limit('SESSION_MESSAGES',2000,20000),
    'account_messages': limit('ACCOUNT_MESSAGES',20000,100000),
    'account_bytes': limit('ACCOUNT_BYTES',128*1024*1024,1024*1024*1024),
    'body_kib': limit('BODY_KIB',256,1024),
    'context_chars': limit('CONTEXT_CHARS',48000,200000),
}

def recent_context(rows):
    """Keep whole recent messages under a character budget; archive stays intact."""
    result, used = [], 0
    for row in reversed(rows):
        size=len(row['content'])
        if used+size>LIMITS['context_chars']:
            break
        result.append({'role':row['role'],'content':row['content']})
        used+=size
    return result[::-1]
