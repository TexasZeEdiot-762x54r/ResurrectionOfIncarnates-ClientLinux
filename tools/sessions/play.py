"""Session used by the launcher: scene changes, crash dumps, missing resources and C runtime errors only
(no per-frame hooks), written to logs/play.log."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from sessions.observe import SCENES  # noqa: E402


def setup(d):
    from sessions import circle_chat_fulltext, today_first_paint
    circle_chat_fulltext.setup(d)
    today_first_paint.setup(d)
    from sessions import version_footer
    version_footer.setup(d)
    from sessions import ranked_search_status
    ranked_search_status.setup(d)
    from sessions import ranked_wait_practice
    ranked_wait_practice.setup(d)
    from sessions import ranked_partial_teams
    ranked_partial_teams.setup(d)
    from sessions import ranked_team_start_trace
    ranked_team_start_trace.setup(d)
    from sessions import team_name_display_trace
    team_name_display_trace.setup(d)
    from sessions import team_name_display
    team_name_display.setup(d)
    from sessions import result_flow
    result_flow.setup(d)
    from sessions import lamina_menu, lamina_combat
    import match_lamina
    match_lamina.setup(d)
    lamina_menu.setup(d)
    lamina_combat.setup(d)
    from sessions import customize_wait_trace
    customize_wait_trace.setup(d)
    from sessions import battle_stats
    battle_stats.setup(d)
    from sessions import first_spawn_hp
    first_spawn_hp.setup(d)
    from sessions import diagnostic_melee
    diagnostic_melee.setup(d)
    from sessions import steel_spine
    steel_spine.setup(d)
    from sessions import level_reward_notice
    level_reward_notice.setup(d)
    from sessions import library_pages
    library_pages.setup(d)
    from sessions import special_thanks
    special_thanks.setup(d)
    from sessions import tutorial_resources
    tutorial_resources.setup(d)
    from sessions import stage_ambience
    stage_ambience.setup(d)
    from sessions import audio_lifetime
    audio_lifetime.setup(d)
    from sessions import circle_voice
    circle_voice.setup(d)
    from sessions import replay_trace
    replay_trace.setup(d)
    from sessions import replay_recording
    replay_recording.setup(d)
    from sessions import cube_completion
    cube_completion.setup(d)
    from sessions import lp_payouts
    if not lp_payouts.setup(d):
        from sessions import lp_result_trace
        lp_result_trace.setup(d)
    def scene_created(name):
        if name in ('Initial','SequencePlay','InitialBattle'):
            record = getattr(d, 'r46_ranked_result', None)
            d.r46_ranked_result = None
            if record and name != 'InitialBattle':
                from ranked_disconnect import notify_returned
                notify_returned(*record)
        d.practice_exit.scene(name)
        d.log(f"SCENE create {name}")
    for addr, name in SCENES.items():
        d.bp(addr, lambda h, n=name: scene_created(n))
    from sessions import summon_sync_trace
    summon_sync_trace.setup(d)

    def res_missing(h):  # 0x140b69dea: rax = resource from 0x1407bb6d0, rbx = requested name
        if h.rax == 0:
            d.log(f"MISSING resource {h.str(h.rbx, 200)!r}")

    d.bp(0x140B69DEA, res_missing)

    def watson(h):
        d.log("INVALID PARAMETER (_invoke_watson); call chain:")
        for i, call, op in d.call_chain(h.rsp, 0x3000)[:20]:
            d.log(f"   stack+{i:x}: call at {d.describe(call)} -> {op}")

    for fn in ("_invoke_watson", "_invalid_parameter", "_invalid_parameter_noinfo"):
        d.bp_export("msvcr100.dll", fn, watson)

    import ranked_disconnect
    ranked_disconnect.setup(d, enabled=True)

    import circle_team_ui
    circle_team_ui.setup(d)

    import peer_profile_warm
    peer_profile_warm.setup(d)

    # Install after reward/roster observers so their callbacks remain chained.
    import lamina_result_phase
    lamina_result_phase.setup(d)

    import ranked_handoff_trace
    ranked_handoff_trace.setup(d)
