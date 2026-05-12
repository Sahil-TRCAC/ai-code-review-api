from celery import shared_task
from django.db import transaction

from .models import Review, Bug, SecurityIssue, QualitySuggestion, ReviewStatus
from .llm_service import LLMService


@shared_task(bind=True, max_retries=1)
def process_review(self, review_id: int):
    try:
        review = Review.objects.get(id=review_id)
        review.status = ReviewStatus.PROCESSING
        review.save()
        
        llm_service = LLMService()
        result = llm_service.review_code(review.code, review.language)
        
        with transaction.atomic():
            review.summary = result.get('summary', '')
            review.score = result.get('score', 0)
            review.raw_llm_response = result
            review.status = ReviewStatus.COMPLETED
            review.save()
            
            Bug.objects.filter(review=review).delete()
            for bug_data in result.get('bugs', []):
                Bug.objects.create(
                    review=review,
                    line=bug_data.get('line'),
                    issue=bug_data.get('issue', ''),
                    severity=bug_data.get('severity', 'low')
                )
            
            SecurityIssue.objects.filter(review=review).delete()
            for sec_data in result.get('security', []):
                SecurityIssue.objects.create(
                    review=review,
                    line=sec_data.get('line'),
                    issue=sec_data.get('issue', ''),
                    severity=sec_data.get('severity', 'medium')
                )
            
            QualitySuggestion.objects.filter(review=review).delete()
            for qual_data in result.get('quality', []):
                QualitySuggestion.objects.create(
                    review=review,
                    suggestion=qual_data.get('suggestion', ''),
                    severity=qual_data.get('severity', 'low')
                )
        
        return {'review_id': review_id, 'status': 'completed'}
        
    except Exception as exc:
        Review.objects.filter(id=review_id).update(status=ReviewStatus.FAILED)
        raise self.retry(exc=exc)
