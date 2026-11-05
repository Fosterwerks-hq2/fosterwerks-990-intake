"""Typed records shared by the MCP server, the agent (step 3) and the eval (step 4)."""
from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    claim: str = Field(description="The specific fact this citation supports, in a few words")
    section_id: str = Field(description="Section ID from search_filings or get_section, "
                                        "e.g. 010559608/2023/990/p1")


class ProspectRecord(BaseModel):
    """What the agent proposes for review. Every number and claim must be backed by a citation
    to a filing section; a value the filings don't show is null, never estimated."""
    ein: str = Field(description="9-digit EIN, digits only")
    name: str
    tax_year: int = Field(description="Tax year of the filing most figures come from")
    form: Literal["990", "990EZ"]
    mission: str | None = Field(description="Mission in a sentence, from the filing")
    total_revenue: int | None = Field(description="Current-year total revenue, whole dollars")
    total_expenses: int | None = Field(description="Current-year total expenses, whole dollars")
    employees: int | None = Field(description="Total employees (Form 990 Part I line 5). "
                                              "Null on a 990-EZ, which doesn't report it")
    programs: list[str] = Field(description="Main programs, a short phrase each")
    sensitive_data: list[str] = Field(
        description="Kinds of regulated or confidential data the work implies (health records, "
                    "student records, client case files...), only where a section shows it")
    fit_notes: str = Field(description="Two or three sentences: why this org is or isn't a "
                                       "good Fosterwerks prospect, from the filing evidence")
    citations: list[Citation] = Field(min_length=1)


class SectionHit(BaseModel):
    id: str
    title: str
    tax_year: int
    form: str
    text: str


class OrgMatch(BaseModel):
    ein: str
    name: str
    ntee: str | None
    filings: list[str] = Field(description='Available filings, newest first, e.g. "2024 990"')


class ReviewItem(BaseModel):
    id: int
    ein: str
    name: str
    status: Literal["pending", "approved", "rejected"]
    proposed_at: str
    proposed_by: str
    actioned_at: str | None = None
    actioned_by: str | None = None
    note: str | None = None
    record: ProspectRecord
