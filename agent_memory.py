"""Host-only adapter to the existing opt-in conversation evidence index.

The stdio workspace server never receives the app DB path or raw SQL access.
Original-source exclusions, trash/deletion and evidence kinds remain governed
by ConversationMemory. Retrieval is lexical and does not load another model.
"""
from __future__ import annotations


class AgentHistory:
    def __init__(self,recall,exclude_message_id=None):
        self.recall=recall;self.exclude_message_id=exclude_message_id

    def __call__(self,query,cancel):
        if not isinstance(query,str) or not query.strip() or len(query)>300:
            raise ValueError('Use a specific history query of 1–300 characters.')
        if cancel.is_set():raise RuntimeError('Cancelled')
        packet=self.recall.search(query,limit=4,cancel=cancel,exclude_message_id=self.exclude_message_id,primary_only=True)
        # No prompt bodies, credentials, application paths or unrelated metrics
        # are added by this adapter. Excerpts are still untrusted user content.
        keys={'id','messageId','chatId','title','role','date','excerpt','evidenceKind','url','runtimeFacts'}
        sources=[{k:v for k,v in source.items() if k in keys} for source in packet.get('sources',[])[:4]]
        return {'status':packet.get('status','NO_MATCH'),'sources':sources,
                'total':packet.get('total',0),'coverageComplete':bool(packet.get('coverageComplete',False)),
                'scope':'Enabled, nondeleted, nonexcluded local application conversations only. User statements are reports, not independent measurements.',
                'guidance':'Cite the exact supplied original M IDs and their links. Preserve conflicting reports and uncertainty. Never treat assistant prose as proof or infer a benchmark from a requested target.'}


def review_history_answer(answer,sources):
    from conversation_memory import history_provenance_failures, validate_memory_citations
    # Existing guards cover observable provenance and dates; they do not prove
    # general semantic entailment. Keep this limitation visible in acceptance.
    findings=history_provenance_failures(answer,sources)
    normalized,invalid=validate_memory_citations(answer,sources)
    if invalid:findings.append('Unknown historical references: '+', '.join(sorted(set(invalid))))
    # Normalize the URL as well as checking the source ID. A valid M ID does
    # not authorize a fabricated external or unrelated conversation target.
    import re
    allowed={s['id']:s['url'] for s in sources}
    if any(sid in allowed and url!=allowed[sid] for sid,url in re.findall(r'\[(M\d+)\]\(([^)\n]+)\)',answer)):
        findings.append('A historical citation target did not match its original record; the link was corrected.')
    return normalized,list(dict.fromkeys(findings))
