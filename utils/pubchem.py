import requests


BASE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"


def get_pubchem_properties(cas_number):
    """
    Fetch PubChem properties using a CAS number.

    Returns:
    {
        success: bool,
        cid: ...,
        name: ...,
        formula: ...,
        weight: ...,
        smiles: ...,
        complexity: ...
    }
    """

    try:
        # --------------------------------------------------
        # STEP 1: CAS -> CID
        # --------------------------------------------------

        cid_url = (
            f"{BASE_URL}/compound/name/"
            f"{cas_number}/cids/JSON"
        )

        cid_response = requests.get(cid_url, timeout=10)

        if cid_response.status_code != 200:
            return {
                "success": False,
                "message": "Compound not found in PubChem"
            }

        cid_data = cid_response.json()

        cid = cid_data["IdentifierList"]["CID"][0]

        # --------------------------------------------------
        # STEP 2: CID -> Properties
        # --------------------------------------------------

        properties = (
            "Title,"
            "MolecularFormula,"
            "MolecularWeight,"
            "CanonicalSMILES,"
            "Complexity"
        )

        property_url = (
            f"{BASE_URL}/compound/cid/"
            f"{cid}/property/{properties}/JSON"
        )

        property_response = requests.get(property_url, timeout=10)

        property_data = property_response.json()

        info = property_data["PropertyTable"]["Properties"][0]

        return {

            "success": True,

            "cid": cid,

            "name": info.get("Title"),

            "formula": info.get("MolecularFormula"),

            "weight": info.get("MolecularWeight"),

            "smiles": info.get("CanonicalSMILES"),

            "complexity": info.get("Complexity")

        }

    except Exception as e:

        return {

            "success": False,

            "message": str(e)

        }