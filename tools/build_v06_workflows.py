#!/usr/bin/env python3
"""Compile the 0.6 workflow baseline against nightly's node schemas.

No archived workflow, widget array, position, or frontend metadata is an input.
Settings and wires use names. --check detects stale generated documents.
"""
from __future__ import annotations
import argparse
import copy
import json
import re
import uuid
from pathlib import Path
from workflow_schema import DATA, ROOT, fields, is_widget, widget_fields, validate_value, load_schemas, options

EXAMPLES = ROOT / 'example_workflows'
NAMESPACE = uuid.UUID('aedf0e28-4e77-4ec8-98d8-7662561e4cf4')
TITLE_HEIGHT, GAP = 30, 70
AUTHOR = {'MiniMaxH3ChainPlanModern', 'MiniMaxH3GenerationProfile', 'MiniMaxH3ChainPlanStudio',
          'MiniMaxH3ProjectAssetManager', 'MiniMaxH3ChainScenePromptEditor',
          'MiniMaxH3ChainRichScenePromptEditor', 'MiniMaxH3ChainCheckpointManager'}
SIZES = {
    'MiniMaxH3ChainPlanModern': (1000,900), 'MiniMaxH3ChainPlanStudio': (1080,940),
    'MiniMaxH3ProjectAssetManager': (1080,880), 'MiniMaxH3ChainCheckpointManager': (1200,900),
    'MiniMaxH3ChainScenePromptEditor': (1000,840), 'MiniMaxH3ChainRichScenePromptEditor': (1200,940),
    'MiniMaxH3ChainReview': (760,880), 'MiniMaxH3ChainCurrent': (460,400),
    'MiniMaxH3ChainUpscaleCurrent': (480,520), 'MiniMaxH3TaggedReferenceToVideo': (520,540),
    'MiniMaxH3ReferenceToVideo': (520,420), 'MiniMaxH3ImageToVideo': (500,380),
    'MiniMaxH3ChainSegmentSave': (460,220), 'MiniMaxH3ChainAssemble': (440,360),
    'MiniMaxH3ChainContext': (420,260), 'MiniMaxH3LoopTrim': (420,250),
    'MiniMaxH3ChainLoopEnd': (420,220), 'MiniMaxH3ChainManifestLoad': (440,200),
    'LoadImage': (400,420), 'LoadImageMask': (400,420), 'LoadVideo': (400,380),
    'VHS_LoadVideo': (400,580), 'PreviewImage': (420,380), 'PreviewAny': (420,180), 'SaveVideo': (480,440),
    'SeedVR2VideoPathUpscaler': (520,580), 'Note': (1000,330),
    'MiniMaxH3ChainUpscalePixelConditioning': (600,520),
    'MiniMaxH3ChainUpscalePixelCurrent': (500,300),
    'UltimateSDUpscaleNoUpscaleGuider': (500,720),
}
LABELS = {
    'MiniMaxH3ChainPlanModern':'Production Plan', 'MiniMaxH3ChainPlanStudio':'Plan Studio',
    'MiniMaxH3ProjectAssetManager':'Project Asset Carousel', 'MiniMaxH3ChainCheckpointManager':'Checkpoint Manager',
    'MiniMaxH3ChainScenePromptEditor':'Scene Prompts', 'MiniMaxH3ChainRichScenePromptEditor':'Scene Prompts + References',
    'MiniMaxH3GenerationProfile':'Generation Profile', 'MiniMaxH3ChainLoopStart':'Loop Start',
    'MiniMaxH3ChainCurrent':'Current Scene', 'MiniMaxH3ChainContext':'Apply Scene Context',
    'MiniMaxH3TaggedReferenceToVideo':'Tagged Ref2VA Conditioning', 'MiniMaxH3LoopTrim':'Trim Carried Overlap',
    'MiniMaxH3ChainSegmentSave':'Save Segment + Checkpoint', 'MiniMaxH3ChainReview':'Review Candidates',
    'MiniMaxH3ChainLoopEnd':'Loop End', 'MiniMaxH3ChainAssemble':'Assemble Final Video',
    'MiniMaxH3ChainManifestLoad':'Load Saved Clips', 'MiniMaxH3ImageToVideo':'Image / Keyframe Conditioning',
    'MiniMaxH3ReferenceToVideo':'Reference Conditioning', 'UNETLoader':'H3 Diffusion Model',
    'CLIPLoader':'H3 Text Encoder', 'RandomNoise':'Scene Seed', 'SamplerCustomAdvanced':'Sample Video + Audio',
    'BasicScheduler':'Sampling Schedule', 'KSamplerSelect':'Sampler',
    'MinimaxH3LatentUpscaler3D':'LBH 3D Latent Upscaler', 'SeedVR2VideoPathUpscaler':'SeedVR2 Video Path Upscaler',
    'MiniMaxH3ChainUpscalePixelCurrent':'Pixel Current Scene • Experimental',
    'MiniMaxH3ChainDeropeBudget':'De-Rope Budget',
    'H3ManualHoldMap':'De-Rope Ranges • RAW Scene Clock',
    'MiniMaxH3ChainUpscalePixelConditioning':'Pixel Conditioning • Actual Image Size',
    'UltimateSDUpscaleNoUpscaleGuider':'USDU H3 • Refine Upscaled Images',
}


def socket_type(spec):
    return 'COMBO' if isinstance(spec[0], (list,tuple)) else spec[0]


def make_node(recipe, schema, number):
    typ, settings = recipe['type'], recipe['settings']
    controls = list(widget_fields(schema, settings))
    assert not (set(settings)-{key for key,_ in controls}), (typ,'unknown settings')
    values=[]
    for key,spec in controls:
        assert key in settings, (typ,key,'recipe must explicitly name every widget')
        validate_value(settings[key],spec,(typ,key))
        values.append(copy.deepcopy(settings[key]))
    inputs=[]
    for name,spec in fields(schema).items():
        if spec[0]=='COMFY_AUTOGROW_V3':
            template=options(spec)['template']
            item_spec=next(iter(template['input']['required'].values()))
            names=[key for key in recipe['inputs'] if key.startswith(name+'.')]
            names.append(name+'.'+template['prefix']+str(len(names)))
            inputs.extend({'name':key,'type':socket_type(item_spec),'link':None} for key in names)
        elif not is_widget(spec) or name in recipe['inputs']:
            value={'name':name,'type':socket_type(spec),'link':None}
            if is_widget(spec): value['widget']={'name':name}
            inputs.append(value)
    label=LABELS.get(typ) or re.sub(r'(?<=[a-z])(?=[A-Z])',' ',typ.replace('MiniMaxH3','').replace('Contex','Context'))
    if typ=='VAELoader': label='Audio VAE' if 'audio' in settings['vae_name'] else 'Video VAE'
    if typ in ('LoadImage','LoadVideo','LoadImageMask'):
        label={'LoadImage':'Reference Image','LoadVideo':'Source Video','LoadImageMask':'Edit Mask'}[typ]
    if recipe.get('mode')==2:label='RECOVERY • '+label
    elif recipe.get('mode')==4:label='OPTIONAL • '+label
    outputs=[dict(name=name,type=kind,links=None) for name,kind in zip(schema['output_name'],schema['output'])]
    input_width=max((len(i['name']) for i in inputs),default=0)*7
    output_width=max((len(o['name']) for o in outputs),default=0)*7
    width=max(360,len(label)*8+48,input_width+output_width+80)
    height=64+max(len(inputs),len(outputs))*20+len(controls)*24
    if any(options(spec).get('multiline') for _,spec in controls):height+=180
    explicit=SIZES.get(typ,(width,height))
    width,height=max(width,explicit[0]),max(height,explicit[1])
    if typ=='VHS_LoadVideo':values=copy.deepcopy(settings)  # VHS uses a named serializer.
    return dict(id=number,type=typ,pos=[0,0],size=[width,height],flags={},order=number-1,
                mode=recipe.get('mode',0),inputs=inputs,outputs=outputs,title=label,
                properties={'Node name for S&R':typ},widgets_values=values)


def group(title,members,color):
    left=min(n['pos'][0] for n in members)-40
    top=min(n['pos'][1] for n in members)-90
    right=max(n['pos'][0]+n['size'][0] for n in members)+40
    bottom=max(n['pos'][1]+n['size'][1] for n in members)+40
    return dict(title=title,bounding=[left,top,right-left,bottom-top],color=color,font_size=24,flags={})


def stack(nodes,x,y):
    for node in nodes:
        node['pos']=[x,y]
        y+=node['size'][1]+TITLE_HEIGHT+GAP
    return y


def layout(nodes,links):
    groups=[]
    author=[n for n in nodes if n['type'] in AUTHOR or n['type']=='Note']
    studio=any(n['type']=='MiniMaxH3ChainPlanStudio' for n in author)
    columns=[[],[],[]] if studio else [[],[]]
    priority={'Note':0,'MiniMaxH3GenerationProfile':1,'MiniMaxH3ChainPlanModern':2,
              'MiniMaxH3ChainPlanStudio':3,'MiniMaxH3ChainScenePromptEditor':3,
              'MiniMaxH3ChainRichScenePromptEditor':3,'MiniMaxH3ProjectAssetManager':4,
              'MiniMaxH3ChainCheckpointManager':5}
    for n in sorted(author,key=lambda n:priority.get(n['type'],9)):
        t=n['type']; col=0
        if studio:
            if t in ('MiniMaxH3ChainPlanStudio','MiniMaxH3ProjectAssetManager'):col=1
            elif t in ('MiniMaxH3ChainRichScenePromptEditor','MiniMaxH3ChainCheckpointManager'):col=2
        elif t in ('MiniMaxH3ChainScenePromptEditor','MiniMaxH3ChainRichScenePromptEditor'):col=1
        columns[col].append(n)
    x=80
    for col in columns:
        if not col:continue
        stack(col,x,180); x+=max(n['size'][0] for n in col)+120
    if author:groups.append(group('01 • PROJECT & SCENES',author,'#315566'))
    x+=100
    runtime=[n for n in nodes if n not in author and n['mode']!=2]
    recovery=sorted([n for n in nodes if n not in author and n['mode']==2],
                    key=lambda n:n['type']!='MiniMaxH3ChainManifestLoad')
    runtime_ids={n['id'] for n in runtime}
    predecessors={n['id']:set() for n in runtime}
    for _,source,_,target,_,_ in links:
        if source in runtime_ids and target in runtime_ids:predecessors[target].add(source)
    depths={}
    while len(depths)<len(runtime):
        ready=[n for n in runtime if n['id'] not in depths and predecessors[n['id']]<=depths.keys()]
        assert ready,'Cycle in generation graph'
        for n in ready:depths[n['id']]=max((depths[p]+1 for p in predecessors[n['id']]),default=0)
    # Adjacent dependency depths share a column, stacked in topological order.
    column_numbers=sorted({depths[n['id']]//2 for n in runtime})
    runtime_left=x
    for index,layer in enumerate(column_numbers):
        col=sorted([n for n in runtime if depths[n['id']]//2==layer],key=lambda n:(depths[n['id']],n['id']))
        stack(col,x,180)
        types={n['type'] for n in col}; heading='PREPARE SCENE'
        if index==0:heading='MODELS & INPUTS'
        if 'SamplerCustomAdvanced' in types:heading='SAMPLE & DECODE'
        if types & {'MiniMaxH3ChainSegmentSave','MiniMaxH3ChainUpscaleSegmentSave'}:heading='SAVE & REVIEW'
        if 'MiniMaxH3ChainReview' in types:heading='REVIEW & CONTINUE'
        if types & {'MiniMaxH3ChainAssemble','SaveVideo'}:heading='DELIVER'
        groups.append(group(f'{index+2:02d} • {heading}',col,'#393f58' if index%2==0 else '#394c49'))
        x+=max(n['size'][0] for n in col)+150
    if recovery:
        y=max((n['pos'][1]+n['size'][1] for n in runtime),default=180)+180
        rx=runtime_left
        for n in recovery:
            n['pos']=[rx,y];rx+=n['size'][0]+130
        groups.append(group('RECOVERY • DISABLED / NO SAMPLING',recovery,'#665135'))
    return groups


def build(recipe,filename,schemas):
    recipe=copy.deepcopy(recipe)
    guides=[n['settings']['text'] for n in recipe['nodes'] if n['type']=='Note']
    records=[n for n in recipe['nodes'] if n['type']!='Note']
    plans=[n for n in records if n['type']=='MiniMaxH3ChainPlanModern']
    for n in records:
        if n['type']=='MiniMaxH3ChainPlanStudio' and plans:
            n['settings'].update({k:v for k,v in plans[0]['settings'].items() if k in n['settings']})
    nodes=[make_node(n,schemas[n['type']],i+1) for i,n in enumerate(records)]
    by_key={r['key']:n for r,n in zip(records,nodes)}
    links=[]
    for record,node in zip(records,nodes):
        for input_name,(source_key,output_name) in record['inputs'].items():
            source=by_key[source_key]
            source_slots=[i for i,s in enumerate(source['outputs']) if s['name'].lower()==output_name.lower()]
            assert len(source_slots)==1,(filename,source_key,output_name,'missing/ambiguous output')
            origin_slot=source_slots[0]
            target_slot=next(i for i,s in enumerate(node['inputs']) if s['name']==input_name)
            inp=node['inputs'][target_slot];out=source['outputs'][origin_slot]
            assert (set(out['type'].split(',')) & set(inp['type'].split(','))
                    or '*' in (out['type'],inp['type'])), (filename,source['type'],out,node['type'],inp)
            link_id=len(links)+1
            links.append([link_id,source['id'],origin_slot,node['id'],target_slot,out['type']])
            inp['link']=link_id;out['links']=(out['links'] or [])+[link_id]
    project_step='Set a unique project run name, then edit scenes and the Generation Profile.'
    if filename.startswith('Deferred'):
        project_step='Select a saved run and lineage in Checkpoint Manager, then set the upscale profile / target size.'
    elif filename.startswith('Masked AV Bridge'):
        project_step='Check both source intervals and the bridge target length; this standalone graph does not use a Plan.'
    recovery_note=('Recovery is disabled by default. Enable it only to assemble saved clips without sampling.\n'
                   if any(n['mode']==2 for n in nodes) else '')
    setup_steps=recipe.get('setup_steps',
        '1. Install models and extra packs listed in example_workflows/README.md.\n'
        '2. Copy the supplied assets to ComfyUI/input; select source media where requested.\n'
        '3. '+project_step+'\n'
        '4. Follow the numbered columns left → right and inspect the saved results.')
    note_text=('NIGHTLY • 0.6 BASELINE • '+filename.removesuffix(' - MiniMax H3 0.6.json')+'\n\n'
        +setup_steps+'\n\n'
        +recovery_note+
        'Workflow-specific setup and detailed wiring notes:\nexample_workflows/guides/'+filename.removesuffix('.json')+'.md')
    note_size=[SIZES['Note'][0],max(SIZES['Note'][1],70+20*len(note_text.splitlines()))]
    nodes.append(dict(id=len(nodes)+1,type='Note',pos=[0,0],size=note_size,flags={},order=len(nodes),mode=0,
        inputs=[],outputs=[],title='START HERE • NIGHTLY',properties={'Node name for S&R':'Note'},widgets_values=[note_text]))
    identity=str(uuid.uuid5(NAMESPACE,filename))
    workflow=dict(id=identity,revision=0,last_node_id=len(nodes),last_link_id=len(links),nodes=nodes,links=links,
        groups=layout(nodes,links),config={},extra={'ds':{'scale':0.65,'offset':[30,0]},
        'comfyui_mcp':{'workflow_uuid':identity}},version=0.4)
    compatibility_note=recipe.get('compatibility_note','Adapted from the **0.6 workflow catalog** for **nightly**.')
    guide='# '+filename.removesuffix('.json')+'\n\n'+compatibility_note+'\n\n'
    guide+=('Setup controls come first, followed by numbered generation columns. '+recovery_note).rstrip()+'\n\n'
    guide+='\n\n---\n\n'.join(guides)+'\n'
    return workflow,guide


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--output-dir',type=Path,default=EXAMPLES)
    parser.add_argument('--workflow',action='append',default=[],
                        help='Build/check only this recipe filename (repeatable).')
    args=parser.parse_args();schemas=load_schemas()
    paths=sorted((DATA/'recipes').glob('*.json'))
    if args.workflow:
        unknown=set(args.workflow)-{p.name for p in paths}
        if unknown:parser.error('Unknown recipes: '+', '.join(sorted(unknown)))
        paths=[p for p in paths if p.name in args.workflow]
    for path in paths:
        workflow,guide=build(json.loads(path.read_text()),path.name,schemas)
        outputs={args.output_dir/path.name:json.dumps(workflow,ensure_ascii=False,indent=2)+'\n',
                 args.output_dir/'guides'/path.with_suffix('.md').name:guide}
        for target,content in outputs.items():
            if args.check:assert target.read_text()==content, f'Stale generated file: {target}'
            else:
                target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding='utf-8')
        print(path.name)


if __name__=='__main__':main()
