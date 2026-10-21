"""有界、可定位的保存拒绝反馈；不修正或省略未通过的输入。"""
import json


class ReportValidationError(ValueError):
    MAX_ISSUES = 24
    MAX_CHARS = 6000

    def __init__(self, issues, *, report_sha256='', measurements=()):
        self.issues=list(issues)
        self.report_sha256=report_sha256
        self.measurements=list(measurements)
        self.feedback=self._feedback()
        super().__init__('尚未保存：'+json.dumps(self.feedback,ensure_ascii=False))

    def _feedback(self):
        payload={'status':'rejected','report_sha256':self.report_sha256,'issues':[],
                 'omitted_issues':len(self.issues),'measurements':self.measurements[:4],
                 'limits':'汇总可独立检查的问题；依赖无效来源的进一步检查待修复后重做。未写入，不自动修正；通过定位也不认证语义。'}
        for issue in self.issues[:self.MAX_ISSUES]:
            item={k:str(issue[k])[:1200] for k in ('path','code','message')}
            if len(str(issue['message']))>1200:
                item['message_truncated']=True
            payload['issues'].append(item)
            payload['omitted_issues']=len(self.issues)-len(payload['issues'])
            if len(json.dumps(payload,ensure_ascii=False))>self.MAX_CHARS:
                payload['issues'].pop()
                payload['omitted_issues']+=1
                break
        return payload
