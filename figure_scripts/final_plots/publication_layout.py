"""Validated publication-only presentation controls; never change scientific data."""
import json
from pathlib import Path
from dataclasses import replace

DEFAULTS={'profile':'auto','width':7.2,'score_height':6.1,'interaction_height':8.6,
          'common_height':2.9,'closeup_height':None,'label_size':6.5,'table_size':8.0,
          'legend_size':6.7,'column_gap':.48,'row_gap':.34,'interaction_mode':'full',
          'receptors':None,'receptor_labels':{},'closeup_row_label_rotation':None,
          'closeup_show_scores':None,'score_include_table':True,'component_height':4.8}
RANGES={'width':(5,20),'score_height':(3,15),'interaction_height':(4,25),'common_height':(2,15),
        'closeup_height':(2,25),'label_size':(4,16),'table_size':(4,16),'legend_size':(4,16),
        'column_gap':(.03,1.5),'row_gap':(.05,1.2),'component_height':(2,15)}

def validate_options(raw=None):
    raw={} if raw is None else raw
    if not isinstance(raw,dict):raise ValueError('Publication layout must be a JSON object')
    unknown=set(raw)-set(DEFAULTS)
    if unknown:raise ValueError('Unknown publication options: '+', '.join(sorted(unknown)))
    options={**DEFAULTS,**raw}
    if options['profile'] not in ['auto','dopamine','serotonin']:raise ValueError('Invalid publication profile')
    if options['interaction_mode'] not in ['full','common','counts','recurrence']:raise ValueError('Invalid interaction_mode')
    if not isinstance(options['score_include_table'],bool):raise ValueError('score_include_table must be boolean')
    if options['closeup_row_label_rotation'] not in [None,0,90]:raise ValueError('closeup_row_label_rotation must be 0 or 90')
    if options['closeup_show_scores'] is not None and not isinstance(options['closeup_show_scores'],bool):
        raise ValueError('closeup_show_scores must be boolean')
    for key,(low,high) in RANGES.items():
        value=options[key]
        if value is None and key=='closeup_height':continue
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not low<=value<=high:
            raise ValueError(f'{key} must be between {low} and {high}')
    if options['receptors'] is not None:
        if not isinstance(options['receptors'],list) or not options['receptors'] or any(not isinstance(v,str) for v in options['receptors']):
            raise ValueError('receptors must be a nonempty list of exact identifiers')
        if len(set(options['receptors']))!=len(options['receptors']):raise ValueError('Duplicate receptor identifiers')
    if not isinstance(options['receptor_labels'],dict) or any(not isinstance(k,str) or not isinstance(v,str) or len(v)>50 for k,v in options['receptor_labels'].items()):
        raise ValueError('Invalid receptor_labels mapping')
    return options

def load_options(path=None):return validate_options(json.loads(Path(path).read_text()) if path else {})

def configured_dataset(data,options):
    receptors=options['receptors'] or data.receptors
    if set(receptors)-set(data.receptors):raise ValueError('Requested receptor absent from dataset')
    labels={**data.receptor_labels,**options['receptor_labels']}
    serotonin_ids={'human_A_8AXD','human_AB_primary','human_AB_sensitivity'}
    if options['profile']=='serotonin' or (options['profile']=='auto' and set(receptors)<=serotonin_ids):
        for r in receptors:
            if r not in options['receptor_labels']:
                labels[r]={'human_A_8AXD':'5-HT3A','human_AB_primary':'5-HT3AB model',
                           'human_AB_sensitivity':'AB sensitivity'}.get(r,labels[r])
    return replace(data,runs=[r for r in data.runs if r.receptor in receptors],
                   receptors=list(receptors),receptor_labels=labels)

def is_serotonin(data,options):
    return options['profile']=='serotonin' or (options['profile']=='auto' and
        set(data.receptors)<={'human_A_8AXD','human_AB_primary','human_AB_sensitivity'})

def archive_options(out,stem,options):
    (Path(out)/(stem+'_layout.json')).write_text(json.dumps(options,indent=2)+'\n')
