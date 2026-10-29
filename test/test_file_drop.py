"""Exercise the drop bridge's event routing, without replacing Gradio upload."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize(
    "mode", ["empty", "nonempty", "native", "outside", "disabled", "text", "remount"]
)
def test_drop_routes_to_native_uploader_once_and_preserves_queue(mode):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required")
    source = (Path(__file__).parents[1] / "pdf2zh/file_drop.js").read_text(
        encoding="utf-8"
    )
    script = r"""
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const mode=process.argv[1];
const listeners=new Map();
const node=(parent=null)=>({parent,contains(other){while(other){if(other===this)return true;other=other.parent;}return false;}});
const host=node(), uploader=node(host), row=node(host), child=node(uploader), outside=node();
host.classList={add(){},remove(){}};
const files=mode==='empty'?[]:['A.pdf'];
let forwarded=0;
const input={disabled:false};
uploader.querySelector=()=>input;
host.querySelector=()=>mode==='disabled'?null:uploader;
const document={getElementById:()=>host,addEventListener(type,fn,options){
  const list=listeners.get(type)||[];list.push({fn,signal:options.signal});listeners.set(type,list);
}};
const window={};
class DragEvent {
  constructor(type,options){this.type=type;Object.assign(this,options);this.defaultPrevented=false;this.stopped=false;}
  preventDefault(){this.defaultPrevented=true;}
  stopPropagation(){this.stopped=true;}
}
function dispatch(event,target){
  event.target=target;
  for(const item of listeners.get(event.type)||[])if(!item.signal.aborted)item.fn(event);
  if(event.type==='drop' && !event.stopped && uploader.contains(target))files.push(...event.dataTransfer.files);
}
uploader.dispatchEvent=event=>{forwarded++;dispatch(event,uploader);};
const init=new Function('window','document','AbortController','DragEvent','return ('+readFileSync(0,'utf8')+')')(window,document,AbortController,DragEvent);
init();if(mode==='remount')init();
const target=mode==='native'?child:mode==='outside'?outside:row;
const event=new DragEvent('drop',{dataTransfer:{types:mode==='text'?['text/plain']:['Files'],files:['B.pdf','C.pdf']}});
dispatch(event,target);
if(['outside','disabled','text'].includes(mode)){
  assert.deepEqual(files,['A.pdf']);assert.equal(forwarded,0);
}else{
  assert.deepEqual(files,mode==='empty'?['B.pdf','C.pdf']:['A.pdf','B.pdf','C.pdf']);
  assert.equal(forwarded,mode==='native'?0:1);
}
assert.equal(event.defaultPrevented,mode!=='text');
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, mode],
        input=source,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
