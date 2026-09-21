"""Treat the gateway's explicit overload response as a transient request failure.

Uses Inspect's existing backoff and episode budgets. Never reruns a sample.
"""
from inspect_ai.model._model import RetryDecision
from inspect_ai.model._openai import OpenAIResponseError
from inspect_ai.model._providers.openai import OpenAIAPI


def install():
    if getattr(OpenAIAPI.should_retry, '_gateway_overload_retry', False):
        return
    original = OpenAIAPI.should_retry

    def should_retry(self, error):
        if isinstance(error, OpenAIResponseError) and error.code == 'server_is_overloaded':
            return RetryDecision.transient(retry_after=15)
        return original(self, error)

    should_retry._gateway_overload_retry = True
    OpenAIAPI.should_retry = should_retry
