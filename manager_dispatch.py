"""Advisory execution boundary; choosing a role grants no native capability."""
import argparse
import json


SUBSTANTIVE = frozenset({'implementation','research','production','investigation','debugging',
    'test_suite','artifact_generation','external_action','automation_rewrite','transcription','repository_edit'})
COORDINATION = frozenset({'planning','briefing','review','reporting','clarification','routing',
    'approval','small_decision','controller_lifecycle','bounded_status_read','manual_morning_summary'})


def plan_execution(actor_kind, categories, *, long_running=False):
    categories=set(categories)
    if not categories or categories-SUBSTANTIVE-COORDINATION:
        return {'boundary':'hold','execution_allowed':False,'reason':'Explicit supported work classification required'}
    if actor_kind=='worker':
        return {'boundary':'execute','execution_allowed':True,'reason':'Worker executes its assigned work'}
    if actor_kind=='friday':
        if categories & SUBSTANTIVE or long_running:
            return {'boundary':'dispatch_worker','execution_allowed':False,
                    'reason':'Friday selects a visible worker or bounded native child, then reviews its result',
                    'missing_capability':'Hold if worker dispatch or required registration cannot be verified'}
        return {'boundary':'coordinate','execution_allowed':False,'reason':'Friday plans, briefs, reviews and reports'}
    return {'boundary':'hold','execution_allowed':False,'reason':'Only a worker or Friday execution role is classified here'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actor',choices=['worker','friday'],required=True)
    parser.add_argument('--category',action='append',required=True)
    args=parser.parse_args()
    print(json.dumps(plan_execution(args.actor,args.category),indent=2))
