"""Versioned tutor intervention contract shared with the web (see apps/web/lib/tutor-contract.ts).

The model emits one structured intervention. The application derives the equivalent text,
the allowed actions and (later) the OpenUI Lang presentation. Nothing here is executable.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "tutor-intervention-v2"

Situation = Literal[
    "ambiguous",  # unclear task or attempt
    "entry",  # clear activity, no attempt yet
    "unsupported_claim",  # claim without evidence
    "partial",  # partial progress
    "contrastable_error",  # error that sources can contradict
    "missing_concept",  # a needed concept is missing
    "blocked",  # stuck or asking for help
    "grounded",  # well-founded answer
    "insufficient_evidence",  # abstention
]
Action = Literal["another_hint", "explain", "view_source"]
DiagramKind = Literal["sequence", "relations"]

_BASE = ConfigDict(extra="forbid", frozen=True)


class Block(BaseModel):
    model_config = _BASE


class QuestionBlock(Block):
    type: Literal["question"]
    text: str = Field(min_length=1, max_length=600)


class HintBlock(Block):
    type: Literal["hint"]
    level: int = Field(ge=1, le=3)
    text: str = Field(min_length=1, max_length=600)


class ExplanationBlock(Block):
    type: Literal["explanation"]
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=1500)


class DiagramNode(BaseModel):
    model_config = _BASE
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,23}$")
    label: str = Field(min_length=1, max_length=80)


class DiagramEdge(BaseModel):
    model_config = _BASE
    source: str
    target: str
    label: str = Field(max_length=80)


class DiagramBlock(Block):
    type: Literal["diagram"]
    kind: DiagramKind
    title: str = Field(min_length=1, max_length=120)
    nodes: list[DiagramNode] = Field(min_length=2, max_length=12)
    edges: list[DiagramEdge] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def consistent(self) -> "DiagramBlock":
        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate diagram node")
        if any(e.source not in ids or e.target not in ids for e in self.edges):
            raise ValueError("diagram edge references an unknown node")
        return self


class ActivityOption(BaseModel):
    model_config = _BASE
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,23}$")
    text: str = Field(min_length=1, max_length=600)


class ActivityBlock(Block):
    type: Literal["activity"]
    question: str = Field(min_length=1, max_length=600)
    options: list[ActivityOption] = Field(min_length=2, max_length=4)
    correct_option_id: str
    hint: str = Field(min_length=1, max_length=600)
    explanation: str = Field(min_length=1, max_length=1500)

    @model_validator(mode="after")
    def consistent(self) -> "ActivityBlock":
        ids = [option.id for option in self.options]
        texts = [option.text for option in self.options]
        if len(set(ids)) != len(ids) or len(set(texts)) != len(texts):
            raise ValueError("duplicate activity option")
        if self.correct_option_id not in ids:
            raise ValueError("activity answer references an unknown option")
        return self


class SourcesBlock(Block):
    """References citations of the same intervention by zero-based index, never free text."""

    type: Literal["sources"]
    citation_indexes: list[int] = Field(min_length=1, max_length=5)


InterventionBlock = Annotated[
    QuestionBlock | HintBlock | ExplanationBlock | DiagramBlock | ActivityBlock | SourcesBlock,
    Field(discriminator="type"),
]


class IntentCitation(BaseModel):
    model_config = _BASE
    chunk_id: UUID
    quote: str = Field(min_length=10, max_length=1600)


class TutorIntervention(BaseModel):
    """Single model output. `abstain`/`clarify` need no citations; factual blocks do."""

    model_config = _BASE
    version: Literal["tutor-intervention-v1", "tutor-intervention-v2"]
    situation: Situation
    blocks: list[InterventionBlock] = Field(min_length=1, max_length=5)
    citations: list[IntentCitation] = Field(max_length=5)
    grounded: bool = Field(strict=True)

    @model_validator(mode="after")
    def coherent(self) -> "TutorIntervention":
        if self.version == "tutor-intervention-v1" and any(
            isinstance(block, ActivityBlock) for block in self.blocks
        ):
            raise ValueError("activity requires tutor-intervention-v2")
        questions = [b for b in self.blocks if isinstance(b, (QuestionBlock, ActivityBlock))]
        if len(questions) > 1:
            raise ValueError("one primary question per turn")
        for block in self.blocks:
            if isinstance(block, SourcesBlock) and any(
                i >= len(self.citations) for i in block.citation_indexes
            ):
                raise ValueError("sources reference a missing citation")
        if self.situation == "insufficient_evidence":
            if (
                self.grounded
                or self.citations
                or any(not isinstance(b, (QuestionBlock, ExplanationBlock)) for b in self.blocks)
            ):
                raise ValueError("abstention carries no evidence")
        elif self.situation == "ambiguous":
            # Coordination only: a single clarifying question and nothing factual.
            if (
                self.grounded
                or self.citations
                or len(self.blocks) != 1
                or not isinstance(self.blocks[0], QuestionBlock)
            ):
                raise ValueError("clarification must be one uncited question")
        elif not self.grounded or not self.citations:
            raise ValueError("factual guidance requires citations")
        return self


def equivalent_text(intervention: TutorIntervention) -> str:
    """Deterministic plain-text rendering of the same content, diagrams included."""
    parts: list[str] = []
    for block in intervention.blocks:
        if isinstance(block, QuestionBlock):
            parts.append(block.text)
        elif isinstance(block, HintBlock):
            parts.append(f"Pista {block.level}: {block.text}")
        elif isinstance(block, ExplanationBlock):
            parts.append(f"{block.title}. {block.text}")
        elif isinstance(block, DiagramBlock):
            labels = {n.id: n.label for n in block.nodes}
            steps = [
                f"{labels[e.source]} → {labels[e.target]}" + (f" ({e.label})" if e.label else "")
                for e in block.edges
            ]
            kind = "Secuencia" if block.kind == "sequence" else "Relaciones"
            parts.append(f"{kind}: {block.title}. " + "; ".join(steps) + ".")
        elif isinstance(block, ActivityBlock):
            options = "\n".join(
                f"{chr(65 + index)}. {option.text}" for index, option in enumerate(block.options)
            )
            parts.append(f"Ahora te toca razonar: {block.question}\n{options}")
        elif isinstance(block, SourcesBlock):
            parts.append("Fuentes: " + " ".join(f"[{i + 1}]" for i in block.citation_indexes) + ".")
    return "\n\n".join(parts)


def allowed_actions(intervention: TutorIntervention) -> list[Action]:
    """The application, not the model, decides which controls a turn exposes."""
    actions: list[Action] = []
    if intervention.situation in {"insufficient_evidence", "ambiguous"}:
        return actions
    if intervention.situation != "grounded":
        actions.append("another_hint")
    actions.append("explain")
    if intervention.citations:
        actions.append("view_source")
    return actions


Mode = Literal["hint", "guided_question", "explanation", "abstain", "clarify"]


def mode_for(intervention: TutorIntervention) -> Mode:
    """Stored/legacy-compatible mode derived by the application, never by the model."""
    if intervention.situation == "insufficient_evidence":
        return "abstain"
    if intervention.situation == "ambiguous":
        return "clarify"
    for block in intervention.blocks:
        if isinstance(block, HintBlock):
            return "hint"
        if isinstance(block, (QuestionBlock, ActivityBlock)):
            return "guided_question"
    return "explanation"


def help_level(intervention: TutorIntervention) -> int:
    """Highest hint level delivered in this intervention (0 when none)."""
    return max((b.level for b in intervention.blocks if isinstance(b, HintBlock)), default=0)
