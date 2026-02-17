import openai_api


def test_build_course_art_prompt_formats_without_key_error():
    prompt = openai_api._build_course_art_prompt("AZ-900", "Microsoft")

    assert "AZ-900" in prompt
    assert "Microsoft" in prompt
    assert '"prerequisites"' in prompt
    assert "{certification}" not in prompt
    assert "{vendor}" not in prompt
