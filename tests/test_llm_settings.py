import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import llm_settings


class LlmSettingsTests(unittest.TestCase):
    def test_verify_uses_structured_response_without_saving_or_leaking_key(self):
        response = {"choices": [{"message": {"tool_calls": [{"type": "function", "function": {
            "name": "classify_titles", "arguments": json.dumps({"classifications": [
                {"id": "connection-check", "category": "keep", "confidence": 1}
            ]})}}]}}]}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            llm_settings, "CONFIG_PATH", Path(directory) / "llm.json"
        ), patch("translate.request_json", return_value=response) as request:
            public = llm_settings.verify_llm_settings("https://example.test/v1", "private-key", "model")
            self.assertTrue(public["verified"])
            self.assertNotIn("private-key", json.dumps(public))
            self.assertFalse(llm_settings.CONFIG_PATH.exists())
            self.assertEqual(request.call_args.kwargs["timeout"], 12)

    def test_verify_rejects_wrong_ids_and_redacts_provider_errors(self):
        from translate import TranslationError
        with patch("translate.request_json", side_effect=TranslationError("private-key echoed by provider")):
            with self.assertRaises(ValueError) as raised:
                llm_settings.verify_llm_settings("https://example.test/v1", "private-key", "model")
        self.assertNotIn("private-key", str(raised.exception))
        with patch("translate.request_json", return_value={"choices": [{"message": {"content": "OK"}}]}):
            with self.assertRaises(ValueError):
                llm_settings.verify_llm_settings("https://example.test/v1", "private-key", "model")

    def test_malformed_provider_shapes_fail_as_validation_errors(self):
        for response in [[], {"choices": ["invalid"]}, {"choices": [{"message": "invalid"}]},
                         {"choices": [{"message": {"tool_calls": ["invalid"]}}]}]:
            with self.subTest(response=response), patch("translate.request_json", return_value=response):
                with self.assertRaises(ValueError):
                    llm_settings.verify_llm_settings("https://example.test/v1", "private-key", "model")

    def test_environment_is_used_until_persistent_config_exists(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            llm_settings, "CONFIG_PATH", Path(directory) / "llm-config.json"
        ), patch.dict(
            os.environ,
            {
                "LLM_BASE_URL": "https://env.example/v1",
                "LLM_API_KEY": "env-secret",
                "LLM_MODEL": "env-model",
            },
            clear=True,
        ):
            settings = llm_settings.read_llm_settings()
        self.assertEqual(settings["model"], "env-model")

    def test_saved_key_is_not_returned_publicly_and_file_is_private(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            llm_settings, "CONFIG_PATH", Path(directory) / "secrets" / "llm-config.json"
        ), patch.dict(os.environ, {}, clear=True):
            public = llm_settings.save_llm_settings(
                "https://provider.example/v1", "top-secret", "luna"
            )
            payload = json.loads(llm_settings.CONFIG_PATH.read_text(encoding="utf-8"))
            mode = stat.S_IMODE(llm_settings.CONFIG_PATH.stat().st_mode)

        self.assertTrue(public["configured"])
        self.assertTrue(public["api_key_set"])
        self.assertNotIn("api_key", public)
        self.assertEqual(payload["api_key"], "top-secret")
        if os.name != "nt":
            self.assertEqual(mode, 0o600)

    def test_blank_key_preserves_existing_key_and_disable_overrides_environment(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            llm_settings, "CONFIG_PATH", Path(directory) / "llm-config.json"
        ), patch.dict(
            os.environ,
            {
                "LLM_BASE_URL": "https://env.example/v1",
                "LLM_API_KEY": "env-secret",
                "LLM_MODEL": "env-model",
            },
            clear=True,
        ):
            llm_settings.save_llm_settings(
                "https://provider.example/v1", "saved-secret", "luna"
            )
            llm_settings.save_llm_settings(
                "https://second.example/v1", "", "luna-2"
            )
            self.assertEqual(llm_settings.read_llm_settings()["api_key"], "saved-secret")
            public = llm_settings.disable_llm_settings()
            self.assertFalse(public["configured"])
            self.assertFalse(llm_settings.llm_is_configured())

    def test_complete_settings_replace_a_corrupt_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            llm_settings, "CONFIG_PATH", Path(directory) / "llm-config.json"
        ), patch.dict(os.environ, {}, clear=True):
            llm_settings.CONFIG_PATH.write_text("not json", encoding="utf-8")
            public = llm_settings.save_llm_settings(
                "https://provider.example/v1", "replacement-secret", "luna"
            )

            self.assertTrue(public["configured"])
            self.assertEqual(llm_settings.read_llm_settings()["model"], "luna")

    def test_base_url_rejects_embedded_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            llm_settings, "CONFIG_PATH", Path(directory) / "llm-config.json"
        ), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "username or password"):
                llm_settings.save_llm_settings(
                    "https://user:pass@provider.example/v1", "secret", "luna"
                )


if __name__ == "__main__":
    unittest.main()
