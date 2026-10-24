#!/usr/bin/env python3
"""
Automated publisher for Eve's OpenBallot Voter Guide (`https://www.openballot.app`).
Once authenticated via Google OAuth in a browser (which sets `__Secure-better-auth.session_token`),
run:
    OPENBALLOT_COOKIE="__Secure-better-auth.session_token=..." python3 publish_eve_guide.py
or pass an explicit Hasura Bearer JWT via `OPENBALLOT_JWT="..."`.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

 payload_path = Path(__file__).parent / "eve_voter_guide_payload.json"
data = json.loads(payload_path.read_text())

GRAPHQL_URL = "https://openballot-prod.hasura.app/v1/graphql"
APP_URL = "https://www.openballot.app"


def get_jwt_and_session(cookie: str):
    req = urllib.request.Request(
        f"{APP_URL}/api/auth/get-graphql-api-jwt",
        headers={"Cookie": cookie, "User-Agent": "AskEveVoterGuidePublisher/1.0"},
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read().decode())
    return body["token"], body.get("session", {})


def gql(token: str, query: str, variables: dict):
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "AskEveVoterGuidePublisher/1.0",
        },
    )
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode())
    if "errors" in res:
        raise RuntimeError(f"GraphQL error: {json.dumps(res['errors'], indent=2)}")
    return res["data"]


def main():
    cookie = os.environ.get("OPENBALLOT_COOKIE", "").strip()
    token = os.environ.get("OPENBALLOT_JWT", "").strip()
    brand_id = os.environ.get("OPENBALLOT_BRAND_ID", "").strip()
    public_group_id = os.environ.get("OPENBALLOT_PUBLIC_GROUP_ID", "").strip()

    if not token:
        if not cookie:
            print("ERROR: Set OPENBALLOT_COOKIE='__Secure-better-auth.session_token=...' or OPENBALLOT_JWT='...'", file=sys.stderr)
            sys.exit(1)
        token, session = get_jwt_and_session(cookie)
        brand_id = brand_id or session.get("activeBrandId", "")
        public_group_id = public_group_id or session.get("publicGroupId", "")

    if not brand_id:
        print("ERROR: Could not determine activeBrandId. Set OPENBALLOT_BRAND_ID.", file=sys.stderr)
        sys.exit(1)

    print(f"[1/6] Updating Brand profile ({brand_id}) -> {data['brand']['name']}...")
    gql(
        token,
        """
        mutation ProfileTitle_UpdateBrandNameMutation($brandId: uuid!, $name: String!) {
          update_brands_by_pk(pk_columns: { id: $brandId }, _set: { name: $name }) { id name }
        }
        """,
        {"brandId": brand_id, "name": data["brand"]["name"]},
    )
    gql(
        token,
        """
        mutation ProfileDescription_UpdateBrandDescriptionMutation($brandId: uuid!, $description: String!) {
          update_brands_by_pk(pk_columns: { id: $brandId }, _set: { description: $description }) { id description }
        }
        """,
        {"brandId": brand_id, "description": data["brand"]["description"]},
    )

    if cookie:
        for slug in [data["brand"]["slug"], data["brand"]["fallback_slug"]]:
            try:
                req = urllib.request.Request(
                    f"{APP_URL}/api/claim-brand-slug",
                    data=json.dumps({"brandId": brand_id, "slug": slug}).encode(),
                    headers={"Cookie": cookie, "Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req) as resp:
                    print(f"[2/6] Claimed vanity URL slug: /g/{slug} ({resp.status})")
                    break
            except Exception as e:
                print(f"[2/6] Note claiming slug {slug}: {e}")

    print("[3/6] Creating or fetching guide for 2026 California General Election...")
    res = gql(
        token,
        """
        mutation GuideYouPageContent_CreateGuideMutation($brandId: uuid!, $electionId: uuid!, $primaryJurisdictions: jsonb!) {
          insert_guides_one(
            object: { brand_id: $brandId, election_id: $electionId, primary_jurisdictions: $primaryJurisdictions }
            on_conflict: { constraint: guides_brand_id_election_id_key, update_columns: [primary_jurisdictions] }
          ) { id }
        }
        """,
        {
            "brandId": brand_id,
            "electionId": data["election"]["id"],
            "primaryJurisdictions": data["election"]["primaryJurisdictions"],
        },
    )
    guide_id = res["insert_guides_one"]["id"]
    print(f"      guideId = {guide_id}")

    print("[4/6] Setting guide 'About' methodology description...")
    gql(
        token,
        """
        mutation GuideAboutTab_UpdateGuideDescriptionMutation($guideId: uuid!, $description: String!) {
          update_guides_by_pk(pk_columns: { id: $guideId }, _set: { description: $description }) { id }
        }
        """,
        {"guideId": guide_id, "description": data["election"]["guideDescription"]},
    )

    print(f"[5/6] Upserting all {len(data['viewpoints'])} ballot item viewpoints...")
    upsert_query = """
    mutation BallotItemCurrentGuideViewpointSection_UpsertViewpointMutation(
      $sourceGuideId: uuid!
      $targetBallotItemId: uuid!
      $selections: [selections_insert_input!]!
      $note: String
    ) {
      delete_viewpoints(
        where: {
          _and: [
            { source_guide_id: { _eq: $sourceGuideId } }
            { target_ballot_item_id: { _eq: $targetBallotItemId } }
          ]
        }
      ) { affected_rows }
      insert_viewpoints_one(
        object: {
          source_guide_id: $sourceGuideId
          target_ballot_item_id: $targetBallotItemId
          note: $note
          selections: { data: $selections }
        }
      ) { id }
    }
    """
    for idx, vp in enumerate(data["viewpoints"], 1):
        sels = [
            {"candidacy_id": s["candidacyId"], "weight": s["weight"], "rank": s["rank"]}
            for s in vp["selections"]
        ]
        gql(
            token,
            upsert_query,
            {
                "sourceGuideId": guide_id,
                "targetBallotItemId": vp["ballotItemId"],
                "selections": sels,
                "note": vp["note"],
            },
        )
        print(f"      [{idx:02d}/{len(data['viewpoints'])}] {vp['slug']} -> {vp['position_label']}")

    print("[6/6] Publishing guide & enabling public discoverability...")
    if public_group_id:
        gql(
            token,
            """
            mutation PublishGuideMutation($publicGroupId: uuid!, $brandId: uuid!) {
              insert_group_brand_relationships_one(
                object: { group_id: $publicGroupId, brand_id: $brandId, type: READER }
                on_conflict: { constraint: group_brand_relationships_group_id_brand_id_type_key, update_columns: [] }
              ) { id }
            }
            """,
            {"publicGroupId": public_group_id, "brandId": brand_id},
        )
    gql(
        token,
        """
        mutation SetGuideDiscoverabilityMutation($guideId: uuid!, $isDiscoverable: Boolean!) {
          update_guides_by_pk(pk_columns: { id: $guideId }, _set: { is_discoverable: $isDiscoverable }) { id is_discoverable }
        }
        """,
        {"guideId": guide_id, "isDiscoverable": True},
    )
    print("SUCCESS! Eve's OpenBallot guide is live.")


if __name__ == "__main__":
    main()
