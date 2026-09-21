"""Loopback Anthropic Messages facade backed by a vLLM OpenAI endpoint."""
from __future__ import annotations
import json, os, threading, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _text(v):
    if isinstance(v, str): return v
    if isinstance(v, list): return ''.join(x.get('text','') for x in v if isinstance(x,dict) and x.get('type')=='text')
    return json.dumps(v, ensure_ascii=False)

def _messages(items):
    out=[]
    for item in items or []:
        role, blocks=item.get('role','user'), item.get('content','')
        if isinstance(blocks,str): out.append({'role':role,'content':blocks}); continue
        texts=[]; calls=[]; results=[]
        for b in blocks or []:
            typ=b.get('type')
            if typ=='text': texts.append(b.get('text',''))
            elif typ=='tool_use': calls.append({'id':b.get('id','call'),'type':'function','function':{'name':b.get('name',''),'arguments':json.dumps(b.get('input',{}))}})
            elif typ=='tool_result': results.append({'role':'tool','tool_call_id':b.get('tool_use_id','call'),'content':_text(b.get('content',''))})
        msg={'role':role,'content':'\n'.join(texts)}
        if not texts and not calls: msg = None
        if calls:
            if msg is None: msg = {'role': role, 'content': None}
            msg['tool_calls']=calls
        if msg: out.append(msg)
        out.extend(results)
    return out

class _Handler(BaseHTTPRequestHandler):
    upstream=''; model=''; api_key=''
    def log_message(self,*_): pass
    def do_POST(self):
        if self.path.rstrip('/')!='/v1/messages': self.send_error(404); return
        try:
            raw=json.loads(self.rfile.read(int(self.headers.get('content-length',0)))); messages=_messages(raw.get('messages'))
            if raw.get('system'): messages.insert(0,{'role':'system','content':_text(raw['system'])})
            streaming = bool(raw.get('stream'))
            body={'model':raw.get('model') or self.model,'messages':messages,'max_tokens':raw.get('max_tokens',1024),'stream':streaming}
            for key in ('temperature','top_p'):
                if key in raw: body[key]=raw[key]
            if 'stop_sequences' in raw: body['stop'] = raw['stop_sequences']
            if raw.get('tools'): body['tools']=[{'type':'function','function':{'name':t['name'],'description':t.get('description',''),'parameters':t.get('input_schema',{'type':'object'})}} for t in raw['tools']]
            if raw.get('tool_choice'): body['tool_choice']=raw['tool_choice']
            headers={'content-type':'application/json'}
            if self.api_key: headers['authorization']='Bearer '+self.api_key
            req=urllib.request.Request(self.upstream.rstrip('/')+'/chat/completions',data=json.dumps(body).encode(),headers=headers)
            with urllib.request.urlopen(req,timeout=1800) as response:
                if streaming:
                    result = {'choices': [{'message': {'role': 'assistant', 'content': '', 'reasoning_content': '', 'tool_calls': []}, 'finish_reason': None}], 'usage': {}}
                    for line in response.read().decode('utf-8', 'replace').splitlines():
                        if not line.startswith('data:') or line[5:].strip() == '[DONE]': continue
                        frame = json.loads(line[5:].strip()); result['id'] = frame.get('id', result.get('id'))
                        result['model'] = frame.get('model', result.get('model'))
                        choice = (frame.get('choices') or [{}])[0]; delta = choice.get('delta') or {}
                        msg = result['choices'][0]['message']; msg['content'] += delta.get('content') or ''
                        msg['reasoning_content'] += delta.get('reasoning_content') or delta.get('reasoning') or ''
                        for call in delta.get('tool_calls') or []:
                            i = call.get('index', 0)
                            while len(msg['tool_calls']) <= i: msg['tool_calls'].append({'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                            dst = msg['tool_calls'][i]; dst['id'] = call.get('id') or dst['id']; fn = call.get('function') or {}
                            dst['function']['name'] += fn.get('name') or ''; dst['function']['arguments'] += fn.get('arguments') or ''
                        if choice.get('finish_reason'): result['choices'][0]['finish_reason'] = choice['finish_reason']
                        if frame.get('usage'): result['usage'] = frame['usage']
                else: result = json.loads(response.read())
            choice=(result.get('choices') or [{}])[0]; msg=choice.get('message') or {}; blocks=[]
            if msg.get('reasoning_content') or msg.get('reasoning'):
                # Preserve local model reasoning in Anthropic's standard
                # thinking block instead of silently dropping it.
                blocks.append({'type':'thinking', 'thinking': msg.get('reasoning_content') or msg.get('reasoning'), 'signature': ''})
            if msg.get('content'): blocks.append({'type':'text','text':msg['content']})
            for call in msg.get('tool_calls') or []:
                fn=call.get('function',{}); blocks.append({'type':'tool_use','id':call.get('id','call'),'name':fn.get('name',''),'input':json.loads(fn.get('arguments') or '{}')})
            stop='tool_use' if msg.get('tool_calls') else ('end_turn' if choice.get('finish_reason')=='stop' else choice.get('finish_reason'))
            out={'id':result.get('id','vllm-anthropic'),'type':'message','role':'assistant','model':result.get('model',body['model']),'content':blocks,'stop_reason':stop,'stop_sequence':None,'usage':result.get('usage',{})}
            if streaming:
                # message_start carries metadata only.  Putting final content
                # here and then emitting content_block deltas duplicates text
                # in Anthropic SDK accumulators.
                start_message = {**out, 'content': [], 'usage': {
                    'input_tokens': out.get('usage', {}).get('prompt_tokens', 0)}}
                events=[{'type':'message_start','message':start_message}]
                for i,b in enumerate(blocks):
                    empty = ({**b, 'text': ''} if b['type'] == 'text' else
                             ({**b, 'thinking': ''} if b['type'] == 'thinking' else b))
                    events.append({'type':'content_block_start','index':i,'content_block':empty})
                    if b['type']=='text': events.append({'type':'content_block_delta','index':i,'delta':{'type':'text_delta','text':b['text']}})
                    elif b['type']=='thinking': events.append({'type':'content_block_delta','index':i,'delta':{'type':'thinking_delta','thinking':b['thinking']}})
                    events.append({'type':'content_block_stop','index':i})
                events += [{'type':'message_delta','delta':{'stop_reason':stop,'stop_sequence':None},'usage':{'output_tokens':out.get('usage', {}).get('completion_tokens', 0)}},{'type':'message_stop'}]
                data=''.join('event: %s\ndata: %s\n\n'%(e['type'],json.dumps(e,ensure_ascii=False)) for e in events).encode(); self.send_response(200); self.send_header('content-type','text/event-stream'); self.end_headers(); self.wfile.write(data); return
            data=json.dumps(out,ensure_ascii=False).encode(); self.send_response(200); self.send_header('content-type','application/json'); self.send_header('content-length',str(len(data))); self.end_headers(); self.wfile.write(data)
        except Exception as exc:
            data=json.dumps({'type':'error','error':{'type':'api_error','message':str(exc)}}).encode(); self.send_response(502); self.send_header('content-type','application/json'); self.send_header('content-length',str(len(data))); self.end_headers(); self.wfile.write(data)

class VLLMProxy:
    def __init__(self,upstream,model=''):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),_Handler); self.server.RequestHandlerClass.upstream=upstream; self.server.RequestHandlerClass.model=model; self.server.RequestHandlerClass.api_key=os.getenv('VLLM_API_KEY',''); self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
    @property
    def base_url(self): return f'http://127.0.0.1:{self.server.server_port}/v1'
    def start(self): self.thread.start(); return self
    def close(self): self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
