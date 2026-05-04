from api_fakes import scalar_all_result


def test_api_client_fixture_can_call_authenticated_route(api_client, db_session_mock):
    db_session_mock.execute.return_value = scalar_all_result([])

    response = api_client.get("/api/v2/employees")
    assert response.status_code == 200
    assert response.json() == []
