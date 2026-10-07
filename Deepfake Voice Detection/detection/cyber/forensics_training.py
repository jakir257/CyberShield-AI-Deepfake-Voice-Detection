"""Django-side controller for Forensics 0.3B fine-tuning."""
import os, re, subprocess, sys, threading, time
from pathlib import Path
from django.conf import settings

BASE=Path(settings.BASE_DIR)
ROOT=BASE.parent.parent
TRAINER=ROOT/'training'/'forensics_finetune.py'
MANIFEST=ROOT/'training'/'forensics_manifest.csv'
FOR_DIR=BASE/'media'/'forensics'
ACTIVE=FOR_DIR/'checkpoints'/'active.safetensors'
_lock=threading.Lock(); _proc=None
_run={'state':'idle','lines':[],'started':None,'finished':None,'error':None,'phase':'','done':0,'total':0}
MAX_LINES=500

def _append(s):
    with _lock:
        _run['lines'].append(s)
        del _run['lines'][:-MAX_LINES]
        m=re.search(r'PROGRESS\s+(\d+)\s+(\d+)\s+(\d+)\s+([0-9.]+)',s)
        if m:
            _run['phase']=f'epoch {m.group(1)}'; _run['done']=int(m.group(2)); _run['total']=int(m.group(3))
        elif s.startswith('EPOCH '): _run['phase']='validating checkpoint'
        elif s.startswith('ACTIVE_CHECKPOINT'): _run['phase']='saving best checkpoint'
        elif s.startswith('TRAINING_DONE'): _run['phase']='finished'

def info():
    from . import forensics_model
    d=forensics_model.describe()
    return {'ready':forensics_model.available(),'base_checkpoint':str(forensics_model.BASE_WEIGHTS),'active_checkpoint':str(ACTIVE) if ACTIVE.exists() else '', 'model':d}

def datasets():
    ready=MANIFEST.is_file()
    detail='manifest ready' if ready else 'Create training/forensics_manifest.csv first.'
    return [{'key':'indicvoices_asvspoof5_mlaad','name':'IndicVoices + ASVspoof 5 + MLAAD','note':'REAL: IndicVoices + ASVspoof 5 bonafide | FAKE: MLAAD + ASVspoof 5 spoof','corpus':str(MANIFEST),'prepared':0,'ready':ready,'detail':detail,'url':'https://huggingface.co/datasets/ai4bharat/IndicVoices','url_label':'IndicVoices','show_url':not ready}]

def start(limit=6000,epochs=2,batch_size=2,grad_accum=4,lr=1e-5):
    global _proc
    if not MANIFEST.is_file(): return False,'Missing training/forensics_manifest.csv. Prepare the three datasets first.'
    with _lock:
        if _run['state']=='running': return False,'Forensics training is already running.'
        _run.update(state='running',lines=[],started=time.time(),finished=None,error=None,phase='starting',done=0,total=0)
    cmd=[sys.executable,str(TRAINER),'--manifest',str(MANIFEST),'--epochs',str(epochs),'--batch-size',str(batch_size),'--grad-accum',str(grad_accum),'--lr',str(lr)]
    if limit: cmd += ['--limit',str(limit)]
    _append('$ '+' '.join(cmd))
    def worker():
        global _proc
        try:
            p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1,encoding='utf-8',errors='replace',env={**os.environ,'PYTHONUNBUFFERED':'1'})
            with _lock: _proc=p
            for line in p.stdout: _append(line.rstrip())
            code=p.wait()
            with _lock:
                _proc=None; _run['finished']=time.time()
                if _run.get('state')=='stopped': return
                _run['state']='done' if code==0 else 'error'
                if code!=0: _run['error']=f'trainer exited {code}'
        except Exception as e:
            with _lock: _proc=None; _run['state']='error'; _run['error']=str(e); _run['finished']=time.time()
        if _run['state']=='done':
            try:
                from . import forensics_model
                forensics_model.reload()
            except Exception as e: _append('WARNING reload failed: '+str(e))
    threading.Thread(target=worker,daemon=True).start()
    return True,'Forensics 0.3B fine-tuning started.'

def stop():
    global _proc
    with _lock:
        p=_proc
        if _run['state']!='running' or p is None: return False,'Nothing is training.'
        _run['state']='stopped'; _run['phase']='stopped'
    try:
        if os.name=='nt': subprocess.run(['taskkill','/F','/T','/PID',str(p.pid)],capture_output=True)
        else: p.kill()
    except Exception: pass
    return True,'Forensics training stopped. The previous active checkpoint is untouched.'

def status():
    with _lock:
        started=_run['started']; elapsed=(time.time()-started) if started else 0
        total=_run['total']; done=_run['done']
        return {'state':_run['state'],'lines':list(_run['lines']),'error':_run['error'],'phase':_run['phase'],'done_clips':done,'total_clips':total,'percent':(done/total*100 if total else 0),'elapsed':elapsed,'dataset':'indicvoices_asvspoof5_mlaad','model':info()['model']}
