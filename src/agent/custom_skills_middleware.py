from typing import Callable, Awaitable

from deepagents.middleware.skills import (
    SkillsMiddleware as BaseSkillsMiddleware,
    SkillMetadata,
    _format_skill_annotations,
)


class SkillsMiddleware(BaseSkillsMiddleware):
    """
    Custom SkillsMiddleware for Solven.
    Same behavior as deepagents.middleware.skills.SkillsMiddleware, but the
    system prompt explicitly instructs the model to use the `load_skill` tool
    to read full skill instructions instead of reading the files directly.
    """

    def _format_skills_list(self, skills: list[SkillMetadata]) -> str:
        """Format skills metadata for display in system prompt.

        Overridden to nudge the model towards using the `load_skill` tool.
        """
        if not skills:
            paths = [f"{source_path}" for source_path in self.sources]
            return (
                f"(No skills available yet. You can create skills in "
                f"{' or '.join(paths)})"
            )

        lines: list[str] = []
        for skill in skills:
            annotations = _format_skill_annotations(skill)
            desc_line = f"- **{skill['name']}**: {skill['description']}"
            if annotations:
                desc_line += f" ({annotations})"
            lines.append(desc_line)

            if skill["allowed_tools"]:
                lines.append(
                    f"  -> Allowed tools: {', '.join(skill['allowed_tools'])}"
                )

            # Key change from the base middleware: instead of telling the model
            # to read the file path directly, we direct it to use the load_skill
            # tool with the path as input.
            lines.append(
                "  -> To read this skill, call the `load_skill` tool with "
                f"the path `{skill['path']}`."
            )

        return "\n".join(lines)

