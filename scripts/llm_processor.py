"""
LLM Processor Module

Generic async LLM processing infrastructure using CLI tools (Gemini, Claude).
Provides subprocess execution with timeout, retry logic with exponential backoff,
and a processor registry.

Usage:
    from llm_processor import PROCESSORS, ProcessingConfig, setup_logging

    processor = PROCESSORS["gemini"]()
    response = await processor.process("Your prompt here")
"""

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ==============================================================================
# CONFIGURATION
# ==============================================================================


@dataclass
class ProcessingConfig:
    """Configuration settings for LLM processing."""

    # File and logging settings
    log_file: Path = Path(".sandbox/history_extractor.log")
    timeout: float = 600.0  # 10 minutes for large prompts

    # Retry configuration
    max_retries: int = 3
    retry_delay: float = 5.0
    retry_backoff_factor: float = 2.0


# Module-level logger
LOGGER = logging.getLogger(__name__)


# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================


def setup_logging(log_file: Optional[Path] = None) -> logging.Logger:
    """Setup logging configuration."""
    if log_file is None:
        log_file = ProcessingConfig().log_file

    log_file.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)33s - %(levelname)8s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    return LOGGER


# ==============================================================================
# CORE CLASSES
# ==============================================================================


class AsyncLLMProcessor:
    """Base processor for CLI-based LLM tools."""

    def __init__(self, tool_name: str, cli_command: str):
        self.tool_name = tool_name
        self.cli_command = cli_command
        self.logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )

    async def _run_subprocess_with_timeout(
        self, command: list[str], timeout: Optional[float] = None
    ) -> tuple[bytes, bytes]:
        """Run subprocess with timeout and proper cleanup."""
        if timeout is None:
            timeout = ProcessingConfig().timeout

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except (OSError, asyncio.TimeoutError):
                    pass
            raise TimeoutError(
                f"CLI process '{' '.join(command[:2])}' timed out after {timeout} seconds"
            )

        if process.returncode != 0:
            stderr_str = stderr.decode()
            raise RuntimeError(
                f"CLI command failed with returncode {process.returncode}: stderr='{stderr_str[:500]}'"
            )

        return stdout, stderr

    async def process(
        self,
        prompt: str,
        timeout: Optional[float] = None,
    ) -> str:
        """Send prompt to LLM CLI and return raw response string.

        Includes retry logic with exponential backoff.
        """
        config = ProcessingConfig()
        last_error = None

        for attempt in range(config.max_retries + 1):
            try:
                if attempt > 0:
                    delay = config.retry_delay * (
                        config.retry_backoff_factor ** (attempt - 1)
                    )
                    self.logger.info(
                        "Retry attempt %s/%s after %.1fs delay",
                        attempt,
                        config.max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)

                command = [self.cli_command, "-p", prompt]
                stdout, stderr = await self._run_subprocess_with_timeout(
                    command, timeout
                )

                response = stdout.decode()
                self.logger.debug(
                    "Response length: %s chars", len(response)
                )

                if attempt > 0:
                    self.logger.info(
                        "Success on retry attempt %s", attempt
                    )

                return response

            except Exception as error:
                last_error = error
                if attempt < config.max_retries:
                    self.logger.warning(
                        "Error on attempt %s: %s",
                        attempt + 1,
                        str(error)[:200],
                    )
                else:
                    self.logger.error(
                        "Failed after %s attempts: %s",
                        attempt + 1,
                        str(error)[:500],
                    )

        raise RuntimeError(
            f"All {config.max_retries + 1} attempts failed. Last error: {last_error}"
        )


class AsyncGeminiProcessor(AsyncLLMProcessor):
    """Gemini processor using CLI subprocess."""

    def __init__(self):
        super().__init__(tool_name="gemini", cli_command="gemini")


class AsyncClaudeProcessor(AsyncLLMProcessor):
    """Claude processor using CLI subprocess."""

    def __init__(self):
        super().__init__(tool_name="claude", cli_command="claude")


# Available processors
PROCESSORS = {
    "gemini": AsyncGeminiProcessor,
    "claude": AsyncClaudeProcessor,
}
