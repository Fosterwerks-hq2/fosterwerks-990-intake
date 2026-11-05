"""Turn one 990 / 990-EZ XML filing into (facts, sections).

facts:    the answer key. A handful of exact values with the XML element each came from.
sections: readable text, split the way the paper form is (Part I, Part III, Part VII...), each
          with a stable ID like "010559608/2023/990/p3" so an answer can cite it and a person
          can find the same place on the printed return.
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NS = "{http://www.irs.gov/efile}"
MAX_SECTION_CHARS = 2500

# Parts of Form 990 in XML order. A part starts at the first of its marker elements.
# (Part I has no marker: it's where every filing starts. Part II is the signature block.)
PART_MARKERS_990 = [
    ("p3", "Part III: program service accomplishments",
     {"MissionDesc", "SignificantNewProgramSrvcInd"}),
    ("p4", "Part IV: checklist of required schedules",
     {"DescribedInSection501c3Ind", "ScheduleBRequiredInd", "PoliticalCampaignActyInd"}),
    ("p5", "Part V: other IRS filings and tax compliance",
     {"IRPDocumentCnt", "IRPDocumentW2GCnt", "EmployeeCnt", "EmploymentTaxReturnsFiledInd"}),
    ("p6", "Part VI: governance, management and disclosure",
     {"GoverningBodyVotingMembersCnt", "IndependentVotingMemberCnt", "FamilyOrBusinessRlnInd"}),
    ("p7", "Part VII: officers, directors, key employees and contractors",
     {"Form990PartVIISectionAGrp", "NoListedPersonsCompensatedInd",
      "TotalReportableCompFromOrgAmt"}),
    ("p8", "Part VIII: statement of revenue",
     {"FederatedCampaignsAmt", "MembershipDuesAmt", "FundraisingAmt", "RelatedOrganizationsAmt",
      "GovernmentGrantsAmt", "AllOtherContributionsAmt", "NoncashContributionsAmt",
      "TotalContributionsAmt", "ProgramServiceRevenueGrp", "TotalRevenueGrp"}),
    ("p9", "Part IX: statement of functional expenses",
     {"GrantsToDomesticOrgsGrp", "GrantsToDomesticIndividualsGrp", "ForeignGrantsGrp",
      "CompCurrentOfcrDirectorsGrp", "OtherSalariesAndWagesGrp", "TotalFunctionalExpensesGrp"}),
    ("p10", "Part X: balance sheet",
     {"CashNonInterestBearingGrp", "SavingsAndTempCashInvstGrp", "PledgesAndGrantsReceivableGrp",
      "AccountsReceivableGrp", "LandBldgEquipCostOrOtherBssAmt", "TotalAssetsGrp"}),
    ("p11", "Part XI: reconciliation of net assets",
     {"ReconcilationRevenueExpnssAmt", "ReconciliationRevenueExpnssAmt",
      "NetUnrealizedGainsLossesInvAmt", "OtherChangesInNetAssetsAmt"}),
    ("p12", "Part XII: financial statements and reporting",
     {"MethodOfAccountingAccrualInd", "MethodOfAccountingCashInd", "AccountantCompileOrReviewInd",
      "FSAuditedInd"}),
]

SCHEDULE_TITLES = {
    "A": "public charity status and public support", "B": "contributors",
    "C": "political and lobbying activities", "D": "supplemental financial statements",
    "E": "schools", "F": "activities outside the United States", "G": "fundraising and gaming",
    "H": "hospitals", "I": "grants to organizations and individuals in the US",
    "J": "compensation information", "K": "tax-exempt bonds", "L": "transactions with interested persons",
    "M": "noncash contributions", "N": "liquidation or disposition of assets",
    "O": "supplemental information", "R": "related organizations",
}


@dataclass
class Section:
    id: str
    title: str
    text: str


@dataclass
class Filing:
    ein: str
    tax_year: int
    form: str
    name: str
    facts: dict = field(default_factory=dict)  # field -> {"value", "source"}
    sections: list[Section] = field(default_factory=list)


# ------------------------------------------------------------------ rendering helpers

_SUFFIX = re.compile(r"(Amt|Cnt|Ind|Txt|Desc|Cd|Nm|Grp|Dt|Yr|Rt|Pct|Num)$")
_ABBREV = {"CY": "current year", "PY": "prior year", "BOY": "beginning of year",
           "EOY": "end of year", "Ofcr": "officer", "Rlt": "related", "Rltd": "related",
           "Org": "organization", "Prgm": "program", "Srvc": "service", "Exps": "expenses",
           "Expns": "expenses", "Comp": "compensation", "Empl": "employee", "Emp": "employee",
           "Bnft": "benefit", "Fndrsng": "fundraising", "Contri": "contribution", "Cntrct": "contract",
           "Rcvd": "received", "Govt": "government", "Invst": "investment", "Acty": "activity",
           "Accom": "accomplishment", "Txbl": "taxable", "Incm": "income", "Ttl": "total",
           "Tot": "total", "Mgmt": "management", "Fincl": "financial", "Stmt": "statement",
           "Rln": "relationship", "Grnt": "grant", "Rcpt": "receipt", "Dir": "director"}


def label(tag: str) -> str:
    base = _SUFFIX.sub("", tag) or tag
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+", base)
    return " ".join(_ABBREV.get(w, w.lower() if not w.isupper() or len(w) > 4 else w)
                    for w in words) or tag


def fmt(tag: str, value: str) -> str:
    v = value.strip()
    if tag.endswith("Ind"):
        return {"X": "Yes", "1": "Yes", "true": "Yes", "0": "No", "false": "No"}.get(v, v)
    if tag.endswith("Amt") and re.fullmatch(r"-?\d+", v):
        n = int(v)
        return f"-${-n:,}" if n < 0 else f"${n:,}"
    return v


def local(el) -> str:
    return el.tag.replace(NS, "")


# Form 990 Part III, line 4a sits at the top level of the XML with generic names.
SPECIAL_LABELS = {
    "Desc": "program 1 (line 4a) description", "ExpenseAmt": "program 1 (line 4a) expenses",
    "GrantAmt": "program 1 (line 4a) grants", "RevenueAmt": "program 1 (line 4a) revenue",
    "ProgSrvcAccomActy2Grp": "program 2 (line 4b)", "ProgSrvcAccomActy3Grp": "program 3 (line 4c)",
    "ProgSrvcAccomActyOtherGrp": "other programs (line 4d)",
    "Form990PartVIISectionAGrp": "officer, director, trustee or key employee",
    "ContractorCompensationGrp": "independent contractor",
}


def label_for(tag: str) -> str:
    return SPECIAL_LABELS.get(tag) or label(tag)


def _inline(el) -> str:
    """One line for a group: 'child: value; child: value' (nested groups flattened)."""
    bits = []
    for c in el:
        if len(c):
            bits.append(_inline(c))
        elif c.text and c.text.strip():
            bits.append(f"{label(local(c))}: {fmt(local(c), c.text)}")
    return "; ".join(b for b in bits if b)


def render(el, prefix: str = "") -> list[str]:
    """Flatten an element into "Label: value" lines. A group of plain fields (a person, an
    address, a revenue line) becomes one line, so it stays together in search results."""
    lines = []
    for c in el:
        tag = local(c)
        if len(c) and all(len(g) == 0 or all(len(h) == 0 for h in g) for g in c):
            lines.append(f"{prefix}{label_for(tag)}: {_inline(c)}")
        elif len(c):
            lines.extend(render(c, f"{prefix}{label_for(tag)} > "))
        elif c.text and c.text.strip():
            lines.append(f"{prefix}{label_for(tag)}: {fmt(tag, c.text)}")
    return lines


def chunk(sec_id: str, title: str, lines: list[str]) -> list[Section]:
    out, buf, size = [], [], 0
    for ln in lines:
        if buf and size + len(ln) > MAX_SECTION_CHARS:
            out.append(buf)
            buf, size = [], 0
        buf.append(ln)
        size += len(ln) + 1
    if buf:
        out.append(buf)
    return [Section(sec_id if i == 0 else f"{sec_id}#{i + 1}",
                    title if len(out) == 1 else f"{title} ({i + 1} of {len(out)})",
                    "\n".join(b)) for i, b in enumerate(out)]


# ------------------------------------------------------------------ facts (answer key)

FACTS_990 = {
    "website": "WebsiteAddressTxt", "formation_year": "FormationYr",
    "mission": "MissionDesc", "activity": "ActivityOrMissionDesc",
    "employees": "TotalEmployeeCnt", "volunteers": "TotalVolunteersCnt",
    "total_revenue": "CYTotalRevenueAmt", "prior_year_revenue": "PYTotalRevenueAmt",
    "total_expenses": "CYTotalExpensesAmt", "salaries_benefits": "CYSalariesCompEmpBnftPaidAmt",
    "program_expenses": "TotalProgramServiceExpensesAmt", "total_assets_eoy": "TotalAssetsEOYAmt",
    "total_liabilities_eoy": "TotalLiabilitiesEOYAmt",
    "net_assets_eoy": "NetAssetsOrFundBalancesEOYAmt", "principal_officer": "PrincipalOfficerNm",
    "voting_board_members": "VotingMembersGoverningBodyCnt",
    "audited_financials": "FSAuditedInd",
}
FACTS_EZ = {
    "website": "WebsiteAddressTxt", "mission": "PrimaryExemptPurposeTxt",
    "total_revenue": "TotalRevenueAmt", "total_expenses": "TotalExpensesAmt",
    "total_assets_eoy": "Form990TotalAssetsGrp/EOYAmt",
    "net_assets_eoy": "NetAssetsOrFundBalancesGrp/EOYAmt",
    "salaries_benefits": "SalariesOtherCompEmplBnftAmt",
}
NUMERIC = {"employees", "volunteers", "total_revenue", "prior_year_revenue", "total_expenses",
           "salaries_benefits", "program_expenses", "total_assets_eoy", "total_liabilities_eoy",
           "net_assets_eoy", "voting_board_members", "formation_year"}


def _facts(form_el, table: dict) -> dict:
    out = {}
    for key, path in table.items():
        el = form_el.find("/".join(NS + p for p in path.split("/")))
        if el is not None and el.text and el.text.strip():
            v = el.text.strip()
            if key in NUMERIC and re.fullmatch(r"-?\d+", v):
                v = int(v)
            elif path.endswith("Ind"):
                v = fmt(path, v)
            out[key] = {"value": v, "source": f"{local(form_el)}/{path}"}
    return out


# ------------------------------------------------------------------ main entry

def parse(xml_bytes: bytes) -> Filing:
    root = ET.fromstring(xml_bytes)
    hdr = root.find(NS + "ReturnHeader")
    ein = hdr.find(f"{NS}Filer/{NS}EIN").text.strip()
    name = hdr.find(f"{NS}Filer/{NS}BusinessName/{NS}BusinessNameLine1Txt").text.strip()
    tax_year = int(hdr.find(NS + "TaxYr").text)
    period_end = hdr.find(NS + "TaxPeriodEndDt").text
    city = hdr.find(f"{NS}Filer/{NS}USAddress/{NS}CityNm")
    data = root.find(NS + "ReturnData")
    main = data.find(NS + "IRS990")
    if main is None:
        main = data.find(NS + "IRS990EZ")
    form = "990EZ" if local(main) == "IRS990EZ" else "990"
    base = f"{ein}/{tax_year}/{form}"

    f = Filing(ein=ein, tax_year=tax_year, form=form, name=name)
    f.facts = _facts(main, FACTS_EZ if form == "990EZ" else FACTS_990)
    f.facts.update({
        "name": {"value": name, "source": "ReturnHeader/Filer/BusinessName"},
        "tax_period_end": {"value": period_end, "source": "ReturnHeader/TaxPeriodEndDt"},
        "form": {"value": form, "source": "ReturnData"},
    })
    if city is not None:
        f.facts["city"] = {"value": city.text.strip(), "source": "ReturnHeader/Filer/USAddress"}

    head = [f"Organization: {name}", f"EIN: {ein}", f"Form: {form}",
            f"Tax year: {tax_year} (period ending {period_end})"]

    if form == "990":
        # Walk the form in order; move to a later part when one of its markers appears.
        # Markers only ever move forward, so a Part I field like NetAssetsOrFundBalancesEOYAmt
        # can't drag us back.
        parts = [("p1", "Part I: summary", list(head))]
        at = -1  # index into PART_MARKERS_990; -1 = Part I
        for c in main:
            tag = local(c)
            for i in range(at + 1, len(PART_MARKERS_990)):
                if tag in PART_MARKERS_990[i][2]:
                    at = i
                    pid, title, _ = PART_MARKERS_990[i]
                    parts.append((pid, title, []))
                    break
            parts[-1][2].extend(render(_wrap(c)))
        for pid, title, lines in parts:
            f.sections.extend(chunk(f"{base}/{pid}", f"Form 990 {title}", lines))
    else:
        f.sections.extend(chunk(f"{base}/main", "Form 990-EZ", head + render(main)))

    for sched in data:
        tag = local(sched)
        if tag == local(main) or not tag.startswith("IRS990Schedule"):
            continue
        letter = tag.replace("IRS990Schedule", "")
        title = f"Schedule {letter}: {SCHEDULE_TITLES.get(letter, '')}".rstrip(": ")
        sid = f"{base}/sched{letter.lower()}"
        if letter == "O":  # one section per explanation, labelled with the form line it explains
            for i, d in enumerate(sched.iter(NS + "SupplementalInformationDetail"), 1):
                ref = d.find(NS + "FormAndLineReferenceDesc")
                txt = d.find(NS + "ExplanationTxt")
                ref_s = ref.text.strip() if ref is not None and ref.text else "note"
                f.sections.extend(chunk(f"{sid}/{i}", f"Schedule O: {ref_s}",
                                        [f"{ref_s}: {txt.text.strip() if txt is not None and txt.text else ''}"]))
        else:
            f.sections.extend(chunk(sid, title, render(sched)))
    return f


def _wrap(el):
    """Render a single top-level element through render() by giving it a parent."""
    parent = ET.Element("wrap")
    parent.append(el)
    return parent
