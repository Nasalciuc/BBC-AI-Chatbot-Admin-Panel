"""Shared pytest configuration — sets mandatory env vars so settings.py
can be imported in tests without a real Supabase/Anthropic connection."""
import os

os.environ.setdefault("SUPABASE_URL", "https://dummy.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "dummy-service-role-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-key")
os.environ.setdefault("JWT_SECRET", "ci-dummy-secret")
