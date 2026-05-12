from rest_framework import serializers
from .models import Review, Bug, SecurityIssue, QualitySuggestion


class BugSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bug
        fields = ['line', 'issue', 'severity']


class SecurityIssueSerializer(serializers.ModelSerializer):
    class Meta:
        model = SecurityIssue
        fields = ['line', 'issue', 'severity']


class QualitySuggestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = QualitySuggestion
        fields = ['suggestion', 'severity']


class ReviewCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ['code', 'language']


class ReviewSerializer(serializers.ModelSerializer):
    bugs = BugSerializer(many=True, read_only=True)
    security_issues = SecurityIssueSerializer(many=True, read_only=True)
    quality_suggestions = QualitySuggestionSerializer(many=True, read_only=True)

    class Meta:
        model = Review
        fields = [
            'id', 'language', 'status', 'summary', 'score',
            'bugs', 'security_issues', 'quality_suggestions',
            'created_at', 'updated_at'
        ]


class ReviewListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ['id', 'language', 'status', 'score', 'summary', 'created_at']
