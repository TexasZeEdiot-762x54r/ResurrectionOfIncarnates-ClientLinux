"""Display-only fallback. Never publish this placeholder or use it for settlement."""


def build(uid, name, permanent, tutorials):
    import roi_data
    import roi_records

    characters = []
    for cid in sorted(roi_data.CHARACTERS):
        base = [str(cid * 1000000 + part * 100000) for part in (1, 2, 3)]
        characters.append(dict(
            sv_charaId=str(cid), sv_charaLv='1', sv_charaEnabled=True,
            sv_charaEnableTerm=permanent, sv_charaTutorial=tutorials,
            sv_charaExp='0', sv_charaUsage='0', sv_activeCustomSlotNum='0',
            sv_customItems=[dict(sv_customItemId=i, sv_customItemHoldId=i) for i in base],
            sv_customSlots=[dict(sv_customItemHoldIds=base + ['0'] * 6)]))
    return dict(
        _id=uid, sv_uid=uid, sv_onlineId=uid, sv_userName=name,
        sv_userRegion='SE', sv_gameRegion='SE', sv_userCountry='SE',
        sv_userCountryState='', sv_userCurrency='EUR', sv_userTutorial=tutorials,
        sv_tutorialMap={}, sv_rankId='1', sv_rankPoint='0',
        sv_winStreakCount='0', sv_winStreakMax='0', sv_playerLv='1',
        sv_playerExp='0', sv_playerStatus='0', sv_virtualCurrency='0',
        sv_gamePoint='0', sv_itemsHoldLimit='9999', sv_allCharaFreeTerm='0',
        sv_freeCharaGainTime='0', sv_selectedStageId='1', sv_skillgrids=[],
        sv_skillcubeHoldLimit='100', sv_skillcubes=[], sv_items=[], sv_onBoostList=[],
        sv_disconnNum='0', sv_circleId='0', sv_activeCharaId='1',
        sv_activeSupLamId='0', sv_characters=characters, sv_supLams=[],
        sv_enteredRoomId='0', sv_tagPartner='0', sv_recipes=[],
        sv_compositeStackLimit='1', sv_compositeStacks=[],
        sv_battleRecords=roi_records.profile_records({}, 'unknown'),
        sv_disconnectLog='0', sv_disconnCriminal='0')
