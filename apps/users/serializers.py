from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from .models import User


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['email', 'username', 'password', 'password_confirm']

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({'password_confirm': 'Passwords do not match'})
        return attrs

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        user = User.objects.create_user(**validated_data)
        return user


class UserSerializer(serializers.ModelSerializer):
    remaining_reviews = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'email', 'username', 'daily_review_count', 'daily_review_reset', 'remaining_reviews', 'created_at']

    def get_remaining_reviews(self, obj):
        return obj.get_remaining_reviews()


class GitHubTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['github_token']

    def update(self, instance, validated_data):
        instance.github_token = validated_data.get('github_token', instance.github_token)
        instance.save()
        return instance
