from app.discover import combine, resolve
from app.schema import Schema


def _attrs(*names, **mx):
    return {n: {"type": "String", "max": mx.get(n)} for n in names}


ACCOUNT = {
    "entitySet": "accounts", "primaryId": "accountid", "primaryName": "name",
    "attrs": _attrs("accountid", "name", "accountnumber", "telephone1", "telephone2", "telephone3", "emailaddress1",
                    "address1_line1", "address1_city", "address1_stateorprovince", "address1_postalcode", "firstname_x"),
    "lookups": [], "options": {},
}
INCIDENT = {
    "entitySet": "incidents", "primaryId": "incidentid", "primaryName": "title",
    "attrs": _attrs("incidentid", "title", "description", "prioritycode", "customerid", description=2000),
    "lookups": [{"attr": "customerid", "referenced": "account", "nav": "customerid_account"},
                {"attr": "customerid", "referenced": "contact", "nav": "customerid_contact"}],
    "options": {"prioritycode": {"high": 1, "normal": 2, "low": 3}},
}
EQUIP = {
    "entitySet": "bdm_equipments", "primaryId": "bdm_equipmentid", "primaryName": "bdm_name",
    "attrs": _attrs("bdm_equipmentid", "bdm_name", "bdm_assetnumber", "bdm_manufacturer", "bdm_plant", "bdm_customerid"),
    "lookups": [{"attr": "bdm_customerid", "referenced": "account", "nav": "bdm_CustomerId"}], "options": {},
}
DRAFT = {
    "entitySet": "bdm_servicerequestdrafts", "primaryId": "bdm_servicerequestdraftid", "primaryName": "bdm_name",
    "attrs": _attrs("bdm_servicerequestdraftid", "bdm_name", "bdm_callid", "bdm_customerid", "bdm_equipmentid", "bdm_ticketid"),
    "lookups": [{"attr": "bdm_customerid", "referenced": "account", "nav": "bdm_CustomerId"},
                {"attr": "bdm_equipmentid", "referenced": "bdm_equipment", "nav": "bdm_EquipmentId"},
                {"attr": "bdm_ticketid", "referenced": "incident", "nav": "bdm_TicketId"}],
    "options": {},
}
ROLES = {"customer": "account", "ticket": "incident", "equipment": "bdm_equipment", "draft": "bdm_servicerequestdraft"}
META = {"account": ACCOUNT, "incident": INCIDENT, "bdm_equipment": EQUIP, "bdm_servicerequestdraft": DRAFT}


def test_resolve_standard_tables():
    s = Schema(combine({"roles": ROLES}, resolve(ROLES, META)))
    assert s.real_table("bdm_customers") == "accounts" and s.real_table("bdm_servicetickets") == "incidents"
    assert s.real_column("bdm_customers", "bdm_primaryphone") == "telephone1"
    assert s.real_column("bdm_servicetickets", "bdm_name") == "title"
    assert s.real_column("bdm_servicetickets", "bdm_status") is None
    assert s.real_column("bdm_servicetickets", "bdm_CustomerId") == "customerid_account"
    assert s.real_column("bdm_servicetickets", "_bdm_customerid_value") == "_customerid_value"
    assert s.real_column("bdm_equipments", "bdm_assetnumber") == "bdm_assetnumber"
    assert s.phone_columns == ["telephone2", "telephone3"]
    assert s.description_max == 2000 and s.note_bind == "objectid_incident"
    assert s.body("bdm_servicetickets", {"bdm_priority": CANON_HIGH})["prioritycode"] == 1


CANON_HIGH = 105730002


def test_explicit_config_wins_over_discovery():
    raw = {"roles": ROLES, "columns": {"bdm_customers": {"bdm_email": "mail"}}, "ticketDescriptionMax": 500}
    s = Schema(combine(raw, resolve(ROLES, META)))
    assert s.real_column("bdm_customers", "bdm_email") == "mail" and s.description_max == 500
    assert s.real_column("bdm_customers", "bdm_primaryphone") == "telephone1"


def test_missing_required_table_and_optional_equipment():
    import pytest

    with pytest.raises(Exception, match="not found"):
        resolve(ROLES, {k: v for k, v in META.items() if k != "incident"})
    out = resolve(ROLES, {k: v for k, v in META.items() if k != "bdm_equipment"})
    assert out["disabled"] == ["bdm_equipments"]


def test_roles_only_config_parses_offline():
    s = Schema({"roles": {"customer": "account", "ticket": "incident", "equipment": None}})
    assert s.logical_name("bdm_customers") == "account" and not s.enabled("bdm_equipments")

CONTACT = {
    "entitySet": "contacts", "primaryId": "contactid", "primaryName": "fullname",
    "attrs": _attrs("contactid", "fullname", "firstname", "lastname", "emailaddress1", "mobilephone", "telephone1", "telephone2", "parentcustomerid"),
    "lookups": [{"attr": "parentcustomerid", "referenced": "account", "nav": "parentcustomerid_account"}], "options": {},
}


def test_contact_role_resolves_and_is_optional():
    roles = {**ROLES, "contact": "contact"}
    s = Schema(combine({"roles": roles}, resolve(roles, {**META, "contact": CONTACT})))
    assert s.contacts and s.real_table("bdm_contacts") == "contacts"
    assert s.real_column("bdm_contacts", "bdm_primaryphone") == "mobilephone"
    assert s.real_column("bdm_contacts", "bdm_CustomerId") == "parentcustomerid_account"
    assert s.contact_phone_columns == ["telephone1", "telephone2"]
    off = Schema(combine({"roles": ROLES}, resolve(ROLES, META)))
    assert not off.contacts
