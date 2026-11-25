"""Private newline-JSON engine worker, run by standalone Python (never Decky)."""
import asyncio
import json
import os
import sys
import traceback

protocol = sys.stdout
sys.stdout = sys.stderr
root, data_dir = sys.argv[1:3]
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(data_dir, 'py_modules'))
from main import Plugin
plugin = Plugin()
plugin.data_dir = data_dir
plugin.config_path = os.path.join(data_dir, 'config.json')
plugin._load_config()
allowed = {'_init_engines', 'get_engine_status', 'run_ocr', 'segment_text', 'lookup_word'}
loop = asyncio.new_event_loop()
for line in sys.stdin:
    try:
        request = json.loads(line)
        method = request['method']
        if method not in allowed:
            raise ValueError('Unsupported engine operation')
        plugin.config = request.get('config', plugin.config)
        result = loop.run_until_complete(getattr(plugin, method)(*request.get('args', [])))
        if method == '_init_engines':
            result = loop.run_until_complete(plugin.get_engine_status())
        response = {'result': result}
    except Exception:
        error = traceback.format_exc()
        print(error, file=sys.stderr, flush=True)
        response = {'error': error}
    protocol.write(json.dumps(response, ensure_ascii=False) + '\n')
    protocol.flush()
