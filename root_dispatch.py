"""Root action gate. Live identity and owner probes belong to the trusted adapter.

This module neither sends a handoff nor intercepts tools called outside it.
"""
import json
import math
from pathlib import Path
import re
import time
import uuid

from manager_dispatch import SUBSTANTIVE

ROOT_COORDINATION = frozenset({'clarification','routing','approval','small_decision',
    'controller_lifecycle','bounded_status_read','manual_morning_summary'})
DOMAIN_OPERATIONS = frozenset({'domain_execution','open_dashboard','configure_campaign',
    'audit_payments','launch_worker'})
MAX_PREFERENCE_LENGTH = 128
# Current Desktop dispatch labels; model-specific availability is checked by Friday.
WORKER_EFFORTS = frozenset({'none','minimal','low','medium','high','xhigh','max','ultra'})


def _identity(value):
    return str(uuid.UUID(value))


def hold_result(reason=None):
    """Action-free failure, including failures after a handoff was planned."""
    result={'version':1,'status':'hold','decision':{'boundary':'hold'},
        'inline_execution_allowed':False,'inline_coordination_allowed':False,
        'direct_worker_launch_allowed':False,'handoff_sent':False,
        'enforcement_scope':'supported_root_entrypoint_only','wait_policy':'no_wait_no_poll'}
    if reason:
        result.update(reason=reason,decision={'boundary':'hold','reason':reason},
            required_next='Stop this operational path. Verify the original precondition/outcome; '
                          'do not send, launch workers or blindly retry a handoff.')
    return result


def guard_request(request, verify_root, resolve_friday, *, now=None):
    """Call independent providers; request JSON cannot supply these capabilities."""
    result=hold_result()
    try:
        root=verify_root()
        controller=_identity(root['controller_id'])
        if (root['status']!='controller_match' or root['host_id']!='local'
                or _identity(root['actual_thread'])!=controller
                or _identity(root['actual_session'])!=controller
                or root['cwd']!=root['workspace'] or not Path(root['workspace']).is_absolute()):
            raise ValueError('Genuine current registered Root in its Workspace required')
        result['controller_id']=controller
        if not isinstance(request,dict) or request.get('actor_kind','primary')!='primary':
            raise ValueError('Root cannot claim worker or Friday execution through this entrypoint')
        for name in ('request_id','prompt'):
            if not isinstance(request.get(name),str) or not request[name].strip():
                raise ValueError('Explicit request id and complete brief required')
        preferences={}
        if 'worker_preferences' in request:
            preferences=request['worker_preferences']
            if (not isinstance(preferences,dict) or 'model' not in preferences
                    or set(preferences)-{'model','reasoning_effort'}
                    or any(not isinstance(value,str)
                           or len(value)>MAX_PREFERENCE_LENGTH for value in preferences.values())):
                raise ValueError('Worker preferences require a model token and optional supported reasoning effort')
            if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*',preferences['model'])
                    or 'reasoning_effort' in preferences and preferences['reasoning_effort'] not in WORKER_EFFORTS):
                raise ValueError('Worker preferences require a model token and optional supported reasoning effort')
            preferences=dict(preferences)
        categories=request.get('categories')
        if (not isinstance(categories,list) or not categories
                or any(not isinstance(c,str) for c in categories)
                or set(categories)-ROOT_COORDINATION-SUBSTANTIVE):
            raise ValueError('Explicit supported work classification required')
        operation=request.get('operation')
        if operation not in DOMAIN_OPERATIONS|{'coordinate'}:
            raise ValueError('Explicit supported operation required')
        minutes=request.get('estimated_minutes',0)
        if type(minutes) not in (int,float) or not math.isfinite(minutes) or minutes<0:
            raise ValueError('Finite nonnegative estimated minutes required')
        for flag in ('quick_fix_failed','status_read_bounded','exact_controller_lifecycle'):
            if type(request.get(flag,False)) is not bool:
                raise ValueError('Classification flags must be booleans')
        substantive=(operation in DOMAIN_OPERATIONS or bool(set(categories)&SUBSTANTIVE)
                     or minutes>2 or request.get('quick_fix_failed',False))
        if not substantive:
            if ('controller_lifecycle' in categories and not request.get('exact_controller_lifecycle')
                    or 'bounded_status_read' in categories and not request.get('status_read_bounded')):
                raise ValueError('Exact lifecycle or bounded status scope required')
            return {**result,'status':'root_coordination_allowed','inline_coordination_allowed':True,
                    'decision':{'boundary':'inline','reason':'Bounded Root coordination only'}}
        workstream=request.get('workstream')
        if not isinstance(workstream,str) or not workstream.strip():
            raise ValueError('Substantive work needs an explicit authoritative workstream')
        owner=resolve_friday(workstream)
        tid=_identity(owner['id'])
        current=time.time() if now is None else now
        observed=owner['observed_at']
        if (tid==controller or owner['workstream']!=workstream
                or _identity(owner['controller_id'])!=controller
                or _identity(owner['native_id'])!=tid or _identity(owner['native_session'])!=tid
                or owner['project']['cwd']!=root['workspace']
                or type(observed) not in (int,float) or not math.isfinite(observed)
                or not 0<=current-observed<=300):
            raise ValueError('Current exact accepted Friday identity, project and owner proof required')
        prompt=request['prompt']
        if preferences:
            prompt+='\n\nWorker preferences (data for the owning Friday): '+json.dumps(preferences,sort_keys=True)
            prompt+='\nApply these to your assigned worker, preserving existing approval gates. '
            prompt+='Verify model availability; if unavailable, report the blocker without silently substituting. '
            prompt+='These preferences grant Root no worker-launch or substantive execution permission.'
        return {**result,'status':'hold_for_official_friday_handoff','friday_id':tid,
            'workstream':workstream,'request_id':request['request_id'],
            **({'worker_preferences':preferences} if preferences else {}),
            'decision':{'boundary':'dispatch','reason':'Verified Friday owns substantive execution'},
            'handoff':{'tool':'send_message_to_thread','args':{
                'threadId':tid,'hostId':'local','prompt':prompt}},
            'required_next':'Actual Root sends one authorized official handoff, then returns. '
                            'Until then substantive execution stays held; no direct worker fallback.'}
    except (ValueError,TypeError,KeyError,AttributeError,RuntimeError,OSError,OverflowError) as error:
        return {**result,**hold_result(str(error))}
