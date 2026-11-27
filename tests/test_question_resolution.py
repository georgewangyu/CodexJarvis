import copy
import json
import time
import unittest
from unittest.mock import patch

import task_watch as w
import incoming_results as incoming
import test_task_watch as fixtures


QUESTION='Keep the whole compilation or make a focused cut?'
CALL='call-synthetic-scope-choice'


def native(answer='Keep the whole compilation',include_result=False):
    reply={'questionItemId':json.dumps(['request_user_input_async',CALL,0]),
           'question':QUESTION,'answer':answer}
    text='<send_user_message_question_reply>\n'+json.dumps([reply])+'\n</send_user_message_question_reply>'
    items=[{'type':'agentMessage','id':CALL,'phase':'final_answer','text':QUESTION,
            'questions':[{'title':QUESTION,'options':['Whole','Focused']}]},
           {'type':'userMessage','id':'human-answer','content':[{'type':'text','text':text}]}]
    if include_result:items.append({'type':'agentMessage','id':'new-result','phase':'final_answer',
                                    'text':'Keeping the whole compilation. New title recommendation is ready.'})
    return {'id':'same-active-turn','status':'inProgress','error':None,'items':items}


class QuestionResolutionTests(unittest.TestCase):
    def test_same_turn_native_answer_resolves_exact_choice_without_approval_or_raw_mutation(self):
        source=w.source(native(answer='No'))
        before=w.encoded(source);fingerprint=w.source_digest(source)
        view=w.question_state(source)
        self.assertEqual(view['active'],[])
        self.assertEqual(view['answered'][0]['answer'],'No')
        self.assertFalse(view['answers_are_approvals'])
        self.assertEqual(before,w.encoded(source));self.assertEqual(fingerprint,w.source_digest(source))
        self.assertTrue(source['questions'])  # Raw source evidence remains intact.

    def test_wrong_question_empty_answer_quoted_reply_and_changed_prompt_stay_pending(self):
        original=native()
        for mutate in ('wrong_id','empty','quoted','changed_question','boolean_index'):
            value=copy.deepcopy(original);user=value['items'][1]['content'][0]
            if mutate=='wrong_id':user['text']=user['text'].replace(CALL,'other-call')
            elif mutate=='empty':user['text']=user['text'].replace('Keep the whole compilation','')
            elif mutate=='quoted':user['text']='Historical example: '+user['text']
            elif mutate=='changed_question':value['items'][0]['questions'][0]['title']='A new question'
            else:user['text']=user['text'].replace(', 0]',', false]')
            view=w.question_state(w.source(value))
            self.assertEqual(len(view['active']),1,mutate);self.assertEqual(view['answered'],[],mutate)

    def test_partial_multiquestion_reply_does_not_close_other_choices(self):
        value=native();value['items'][0]['questions'].append({'title':'Which layout?'})
        view=w.question_state(w.source(value))
        self.assertEqual(view['active'][0]['index'],1)
        self.assertEqual(view['resolved_request_ids'],[])

    def test_official_ui_projection_recovers_only_exact_unchanged_question_render(self):
        value=native();reference=w.source(value);before=w.encoded(reference)
        projected=copy.deepcopy(value);projected['items'][0].pop('questions')
        self.assertEqual(w.question_state(w.source(projected))['answered'],[])
        observed=w.observed_source(projected,reference)
        self.assertEqual(w.source_digest(observed),w.source_digest(reference))
        self.assertEqual(len(w.question_state(observed)['answered']),1)
        self.assertEqual(w.encoded(reference),before)
        projected['items'][0]['text']='A changed UI question'
        self.assertNotEqual(w.source_digest(w.observed_source(projected,reference)),w.source_digest(reference))

    def test_answer_only_notice_is_quiet_but_new_result_excerpt_omits_old_ask(self):
        owner=fixtures.TrackingTests();owner.setUp();self.addCleanup(owner.tearDown)
        value=native();owner.reader.current.insert(0,value)
        event=w.reconcile_task(owner.root,owner.reader,fixtures.WORKER)['events'][0]
        state=w.status(owner.root)
        self.assertEqual(next(n for n in state['notice'] if n['fact']==event)['state'],'suppressed_nonactionable')
        original_fact=next(f for f in state['fact'] if f['id']==event)
        self.assertIn(QUESTION,original_fact['source'])
        owner.reader.current[0]=native(include_result=True)
        new=w.reconcile_task(owner.root,owner.reader,fixtures.WORKER)['events'][0]
        packet=json.loads(w.notice_path(owner.root,new,fixtures.HELPER).read_text())
        self.assertNotIn(QUESTION,packet['result_excerpt'])
        self.assertIn('Keeping the whole compilation',packet['result_excerpt'])
        self.assertEqual(packet['question_state']['active'],[])
        self.assertEqual(next(n for n in w.status(owner.root)['notice'] if n['fact']==new)['state'],'pending')

    def test_consumer_cannot_reask_answered_native_question_as_new_decision(self):
        value=native(include_result=True);fact=w.source(value)
        packet={'source_sha256':__import__('hashlib').sha256(w.encoded(fact).encode()).hexdigest(),
                'generation':'synthetic-generation','source_thread':fixtures.WORKER,
                'source_turn':value['id'],'recipient':fixtures.ROOT}
        fresh={'observed_at':time.time(),'official_item':'synthetic-current-read',
               'read':{'thread':{'id':fixtures.WORKER},'turns':[value]}}
        judgment={'generation':packet['generation'],'source_sha256':packet['source_sha256'],
                  'kind':'decision','new':True,'key':'scope','detail':QUESTION,'summary':'Please choose again',
                  'reason':'Old native question incorrectly treated as pending','question_id':CALL,'question_index':0}
        result=incoming.plan_result(packet,fact,fresh,judgment,'/synthetic/workspace')
        self.assertEqual(result['disposition'],'quiet')
        judgment['kind']='blocker'
        self.assertEqual(incoming.plan_result(packet,fact,fresh,judgment,'/synthetic/workspace')['disposition'],'quiet')
        judgment['kind']='decision'
        judgment.pop('question_id');judgment.pop('question_index')
        self.assertEqual(incoming.plan_result(packet,fact,fresh,judgment,'/synthetic/workspace')['disposition'],'hold')
        judgment['kind']='blocker'
        self.assertEqual(incoming.plan_result(packet,fact,fresh,judgment,'/synthetic/workspace')['disposition'],'hold')
        judgment['kind']='decision'
        judgment.update(decision_origin='owner_assessment',evidence=['new-independent-owner-choice'],
                        detail='New publication scheduling choice',summary='Choose a new publication time')
        self.assertEqual(incoming.plan_result(packet,fact,fresh,judgment,'/synthetic/workspace')['disposition'],'surface')


if __name__=='__main__':unittest.main()
