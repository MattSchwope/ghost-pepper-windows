from gpw.cli import build_parser


def test_record_audio_command_parses_duration():
    parser = build_parser()
    args = parser.parse_args(["record-audio", "--duration", "3"])
    assert args.command == "record-audio"
    assert args.duration == 3
