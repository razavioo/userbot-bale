"""Bale gRPC service paths derived from bale.apk jadx string constants."""

from __future__ import annotations

from functools import lru_cache

# Source: docs/apk/service_paths.md (regenerate after jadx).
SERVICE_PATHS: dict[str, tuple[str, ...]] = {
    'bale.abacus.v1.Abacus': ('/bale.abacus.v1.Abacus/EnableShowReactionFlag', '/bale.abacus.v1.Abacus/GetMessagesViews', '/bale.abacus.v1.Abacus/GetShowReactionFlag', '/bale.abacus.v1.Abacus/LoadReactions', '/bale.abacus.v1.Abacus/MessageReactionsRead', '/bale.abacus.v1.Abacus/MessageRemoveReaction', '/bale.abacus.v1.Abacus/MessageSetReaction'),
    'bale.advertisement.v1.Advertisement': ('/bale.advertisement.v1.Advertisement/ChangeChannelIncomeOwner', '/bale.advertisement.v1.Advertisement/GetChannelIncomeReport', '/bale.advertisement.v1.Advertisement/GetVODContents', '/bale.advertisement.v1.Advertisement/UpdateClick', '/bale.advertisement.v1.Advertisement/UpdateView'),
    'bale.appzar.v1.Appzar': ('/bale.appzar.v1.Appzar/GetMenuButton', '/bale.appzar.v1.Appzar/GetMiniAppUrl', '/bale.appzar.v1.Appzar/InvokeCustomMethod'),
    'bale.auth.v1.Auth': ('/bale.auth.v1.Auth/ChangeLanguage', '/bale.auth.v1.Auth/DeleteAccount', '/bale.auth.v1.Auth/DisableTwoFactorAuthentication', '/bale.auth.v1.Auth/EnableTwoFactorAuthentication', '/bale.auth.v1.Auth/GetAuthSessions', '/bale.auth.v1.Auth/GetJWTToken', '/bale.auth.v1.Auth/IsTwoFactorAuthenticationEnabled', '/bale.auth.v1.Auth/RecoverPassword', '/bale.auth.v1.Auth/SendDeleteAccountVerificationCode', '/bale.auth.v1.Auth/SetNewPassword', '/bale.auth.v1.Auth/SignOut', '/bale.auth.v1.Auth/TerminateAllSessions', '/bale.auth.v1.Auth/TerminateSession', '/bale.auth.v1.Auth/VerifyEmail', '/bale.auth.v1.Auth/VerifyPassword', '/bale.auth.v1.Auth/VerifyPasswordRecovery'),
    'bale.balebank.v1.GoldGiftPacket': ('/bale.balebank.v1.GoldGiftPacket/OpenGoldGiftPacket',),
    'bale.balebank.v1.GoldWallet': ('/bale.balebank.v1.GoldWallet/GetBalance',),
    'bale.bank.v1.Bank': ('/bale.bank.v1.Bank/BuyFastCharge', '/bale.bank.v1.Bank/GetOTPToken', '/bale.bank.v1.Bank/GetPaymentToken', '/bale.bank.v1.Bank/GetRecentCharges', '/bale.bank.v1.Bank/GetRemainToken', '/bale.bank.v1.Bank/GetSadadPSPPaymentToken', '/bale.bank.v1.Bank/GrantBankiAccess'),
    'bale.crowdfunding.v1.CrowdFunding': ('/bale.crowdfunding.v1.CrowdFunding/GetTotalPaidAmount',),
    'bale.falake.v1.Falake': ('/bale.falake.v1.Falake/GetLinkStatus',),
    'bale.fanoos.v1.fanoos': ('/bale.fanoos.v1.fanoos/Send', '/bale.fanoos.v1.fanoos/SendBatch'),
    'bale.feedback.v1.FeedBack': ('/bale.feedback.v1.FeedBack/SendFeedBack',),
    'bale.garson.v1.Garson': ('/bale.garson.v1.Garson/GetServices', '/bale.garson.v1.Garson/SearchServices'),
    'bale.ghasedak.v1.GhasedakService': ('/bale.ghasedak.v1.GhasedakService/GetDiff', '/bale.ghasedak.v1.GhasedakService/GetRoutesStates'),
    'bale.giftpacket.v1.GiftPacket': ('/bale.giftpacket.v1.GiftPacket/OpenGiftPacket', '/bale.giftpacket.v1.GiftPacket/SendGiftPacketWithWallet'),
    'bale.gpt_bot.v1.Gpt': ('/bale.gpt_bot.v1.Gpt/EnableAi', '/bale.gpt_bot.v1.Gpt/GetContextMenuOptions', '/bale.gpt_bot.v1.Gpt/IsAiEnabled'),
    'bale.groups.v1.Groups': ('/bale.groups.v1.Groups/AddDiscussionGroupAdmin', '/bale.groups.v1.Groups/CreateGroup', '/bale.groups.v1.Groups/EditChannelNick', '/bale.groups.v1.Groups/EditGroupAbout', '/bale.groups.v1.Groups/EditGroupAvatar', '/bale.groups.v1.Groups/EditGroupTitle', '/bale.groups.v1.Groups/FetchGroupAdmins', '/bale.groups.v1.Groups/GetBannedUsers', '/bale.groups.v1.Groups/GetFullGroup', '/bale.groups.v1.Groups/GetGroupInviteURL', '/bale.groups.v1.Groups/GetMemberPermissions', '/bale.groups.v1.Groups/GetMutualGroups', '/bale.groups.v1.Groups/GetMyGroups', '/bale.groups.v1.Groups/GetPins', '/bale.groups.v1.Groups/InviteUser', '/bale.groups.v1.Groups/JoinGroup', '/bale.groups.v1.Groups/JoinPublicGroup', '/bale.groups.v1.Groups/KickUser', '/bale.groups.v1.Groups/LeaveGroup', '/bale.groups.v1.Groups/LoadGroupAvatars', '/bale.groups.v1.Groups/LoadGroups', '/bale.groups.v1.Groups/LoadMembers', '/bale.groups.v1.Groups/MakeUserAdmin', '/bale.groups.v1.Groups/PinMessage', '/bale.groups.v1.Groups/RemoveDiscussionGroup', '/bale.groups.v1.Groups/RemoveGroupAvatar', '/bale.groups.v1.Groups/RemovePin', '/bale.groups.v1.Groups/RemoveSinglePin', '/bale.groups.v1.Groups/RemoveUserAdmin', '/bale.groups.v1.Groups/RevokeInviteURL', '/bale.groups.v1.Groups/SetAvailableReactions', '/bale.groups.v1.Groups/SetCanSeeMessages', '/bale.groups.v1.Groups/SetGroupDefaultPermissions', '/bale.groups.v1.Groups/SetMemberCustomTitle', '/bale.groups.v1.Groups/SetMemberPermissions', '/bale.groups.v1.Groups/SetRestriction', '/bale.groups.v1.Groups/SetSignMessages', '/bale.groups.v1.Groups/SetSlowMode', '/bale.groups.v1.Groups/TransferOwnership', '/bale.groups.v1.Groups/UnBanUser'),
    'bale.ketf.v1.Ketf': ('/bale.ketf.v1.Ketf/InvokeCustomAction', '/bale.ketf.v1.Ketf/SendAuthenticatedInlineCallBackData', '/bale.ketf.v1.Ketf/SendInlineCallback', '/bale.ketf.v1.Ketf/SendMiniAppData'),
    'bale.kifpool.v1.Kifpool': ('/bale.kifpool.v1.Kifpool/GetMyKifpools',),
    'bale.lahze.v1.Lahze': ('/bale.lahze.v1.Lahze/GetGroupsLives', '/bale.lahze.v1.Lahze/GetLiveInfo'),
    'bale.magazine.v1.Magazine': ('/bale.magazine.v1.Magazine/GetMessageUpvoters', '/bale.magazine.v1.Magazine/GetMyUpvotes', '/bale.magazine.v1.Magazine/GetSimilarPosts', '/bale.magazine.v1.Magazine/LoadCategories', '/bale.magazine.v1.Magazine/RevokeUpvotedPost', '/bale.magazine.v1.Magazine/UpvotePost'),
    'bale.maviz.v1.MavizStream': ('/bale.maviz.v1.MavizStream/SubscribeToThreadUpdates', '/bale.maviz.v1.MavizStream/UnsubscribeFromThreadUpdates'),
    'bale.meet.v1.Meet': ('/bale.meet.v1.Meet/AcceptCall', '/bale.meet.v1.Meet/AskToJoinCall', '/bale.meet.v1.Meet/DeleteCallLogs', '/bale.meet.v1.Meet/DiscardCall', '/bale.meet.v1.Meet/GenerateCallLink', '/bale.meet.v1.Meet/GetCallLinkDetails', '/bale.meet.v1.Meet/GetCallLogs', '/bale.meet.v1.Meet/GetGroupCall', '/bale.meet.v1.Meet/GetOngoingCalls', '/bale.meet.v1.Meet/GetWssURL', '/bale.meet.v1.Meet/InviteToCall', '/bale.meet.v1.Meet/JoinGroupCall', '/bale.meet.v1.Meet/LeaveGroupCall', '/bale.meet.v1.Meet/MuteParticipant', '/bale.meet.v1.Meet/ReceiveCall', '/bale.meet.v1.Meet/RemoveParticipant', '/bale.meet.v1.Meet/SendCallReaction', '/bale.meet.v1.Meet/SetLinkTitle', '/bale.meet.v1.Meet/StartCall', '/bale.meet.v1.Meet/StartGroupCall', '/bale.meet.v1.Meet/StartRecording', '/bale.meet.v1.Meet/StopRecording', '/bale.meet.v1.Meet/SubmitCallFeedback'),
    'bale.messaging.v2.Messaging': ('/bale.messaging.v2.Messaging/ClearChat', '/bale.messaging.v2.Messaging/CreateFolder', '/bale.messaging.v2.Messaging/CreateReservedFolder', '/bale.messaging.v2.Messaging/CreateTopic', '/bale.messaging.v2.Messaging/DeleteChat', '/bale.messaging.v2.Messaging/DeleteFolder', '/bale.messaging.v2.Messaging/DeleteMessage', '/bale.messaging.v2.Messaging/DeleteTopic', '/bale.messaging.v2.Messaging/EditFolder', '/bale.messaging.v2.Messaging/EditTopic', '/bale.messaging.v2.Messaging/ForwardMessages', '/bale.messaging.v2.Messaging/GetMessagesRepliesInfo', '/bale.messaging.v2.Messaging/GetTopics', '/bale.messaging.v2.Messaging/LoadFolders', '/bale.messaging.v2.Messaging/LoadGroupedDialogs', '/bale.messaging.v2.Messaging/LoadHistory', '/bale.messaging.v2.Messaging/LoadPeerDialogs', '/bale.messaging.v2.Messaging/LoadPinnedMessages', '/bale.messaging.v2.Messaging/LoadReplies', '/bale.messaging.v2.Messaging/MarkDialogsAsRead', '/bale.messaging.v2.Messaging/MarkDialogsAsUnread', '/bale.messaging.v2.Messaging/MentionRead', '/bale.messaging.v2.Messaging/MessageRead', '/bale.messaging.v2.Messaging/PinMessage', '/bale.messaging.v2.Messaging/ReorderFolders', '/bale.messaging.v2.Messaging/SendMessage', '/bale.messaging.v2.Messaging/SendMultiMediaMessage', '/bale.messaging.v2.Messaging/UnPinMessages', '/bale.messaging.v2.Messaging/UnpinDialogs', '/bale.messaging.v2.Messaging/UpdateMessage'),
    'bale.microbanki.v1.MicroBanki': ('/bale.microbanki.v1.MicroBanki/GetBamServiceToken', '/bale.microbanki.v1.MicroBanki/GetMoneyRequestDetails', '/bale.microbanki.v1.MicroBanki/GetMoneyRequestPaymentList'),
    'bale.organizations.v1.Organizations': ('/bale.organizations.v1.Organizations/GetUserOrganizationInfo', '/bale.organizations.v1.Organizations/GetUserOrganizationalContacts'),
    'bale.passport.v1.Passport': ('/bale.passport.v1.Passport/GetPassportGroup', '/bale.passport.v1.Passport/GetPassportGroupList', '/bale.passport.v1.Passport/LoadFormLink', '/bale.passport.v1.Passport/RemovePassportInfo', '/bale.passport.v1.Passport/SetPassportGroup', '/bale.passport.v1.Passport/SetPassportInfoByLink', '/bale.passport.v1.Passport/SubmitForm', '/bale.passport.v1.Passport/ValidateField'),
    'bale.pfm.v1.Pfm': ('/bale.pfm.v1.Pfm/AddDetailToTransaction', '/bale.pfm.v1.Pfm/AddTransactionTags', '/bale.pfm.v1.Pfm/AddUserTags', '/bale.pfm.v1.Pfm/FilterTaggedTransactions', '/bale.pfm.v1.Pfm/GetUserAccounts', '/bale.pfm.v1.Pfm/GetUserTags', '/bale.pfm.v1.Pfm/LoadTransactions', '/bale.pfm.v1.Pfm/RemoveTransaction', '/bale.pfm.v1.Pfm/RemoveUserTags'),
    'bale.pishvaz.v1.Pishvaz': ('/bale.pishvaz.v1.Pishvaz/GetMarketingToolsConfig', '/bale.pishvaz.v1.Pishvaz/GetOnboardingPageData', '/bale.pishvaz.v1.Pishvaz/SetMarketingToolAction'),
    'bale.poll.v1.Poll': ('/bale.poll.v1.Poll/ClosePoll', '/bale.poll.v1.Poll/CreatePoll', '/bale.poll.v1.Poll/GetFullPollResult', '/bale.poll.v1.Poll/GetPollResults', '/bale.poll.v1.Poll/Vote'),
    'bale.premium.v1.Premium': ('/bale.premium.v1.Premium/CalculateDiscountedPrice', '/bale.premium.v1.Premium/GetBadges', '/bale.premium.v1.Premium/GetPackages', '/bale.premium.v1.Premium/IsPremium', '/bale.premium.v1.Premium/IsPremiumBatch', '/bale.premium.v1.Premium/PurchasePackage', '/bale.premium.v1.Premium/SetUserBadge'),
    'bale.presence.v1.Presence': ('/bale.presence.v1.Presence/GetGroupOnlineCount', '/bale.presence.v1.Presence/GetUsersPresence', '/bale.presence.v1.Presence/SetOnline', '/bale.presence.v1.Presence/StopTyping', '/bale.presence.v1.Presence/SubscribeToOnline', '/bale.presence.v1.Presence/Typing'),
    'bale.ramz.v1.Ramz': ('/bale.ramz.v1.Ramz/CheckPassword', '/bale.ramz.v1.Ramz/CheckPasswordSet', '/bale.ramz.v1.Ramz/DeletePassword', '/bale.ramz.v1.Ramz/ForgetPassword', '/bale.ramz.v1.Ramz/SendOTP', '/bale.ramz.v1.Ramz/SetPassword', '/bale.ramz.v1.Ramz/ValidateOTP'),
    'bale.recommender.v1.Recommender': ('/bale.recommender.v1.Recommender/GetRelatedChannels',),
    'bale.report.v1.Report': ('/bale.report.v1.Report/ReportDismiss', '/bale.report.v1.Report/ReportInappropriateContent'),
    'bale.sap.v1.Sap': ('/bale.sap.v1.Sap/AddDestinationCards', '/bale.sap.v1.Sap/AddNewCards', '/bale.sap.v1.Sap/DeliverOtp', '/bale.sap.v1.Sap/EditCardExpirationDate', '/bale.sap.v1.Sap/EnrollNewCard', '/bale.sap.v1.Sap/GetCardInfo', '/bale.sap.v1.Sap/GetCards', '/bale.sap.v1.Sap/GetDestinationCardInfo', '/bale.sap.v1.Sap/GetDestinationCards', '/bale.sap.v1.Sap/ReactivateApp', '/bale.sap.v1.Sap/RemoveCard', '/bale.sap.v1.Sap/RemoveDestinationCards', '/bale.sap.v1.Sap/TransferMoneyByCard'),
    'bale.schedule.v1.Scheduler': ('/bale.schedule.v1.Scheduler/ExecuteTaskNow', '/bale.schedule.v1.Scheduler/PeersWithScheduleTask', '/bale.schedule.v1.Scheduler/ReScheduleTask', '/bale.schedule.v1.Scheduler/ScheduleTask', '/bale.schedule.v1.Scheduler/UnScheduleTask'),
    'bale.search.v1.Search': ('/bale.search.v1.Search/RecommendPeer', '/bale.search.v1.Search/SearchContent', '/bale.search.v1.Search/SearchMembers', '/bale.search.v1.Search/SearchMessageMore', '/bale.search.v1.Search/SearchMessages', '/bale.search.v1.Search/SearchPeer', '/bale.search.v1.Search/UpdateSearchContentClick'),
    'bale.shared_media.v1.SharedMediaService': ('/bale.shared_media.v1.SharedMediaService/GetActiveSharedMedia', '/bale.shared_media.v1.SharedMediaService/LoadMedia'),
    'bale.story.v1.Story': ('/bale.story.v1.Story/AddBotStory', '/bale.story.v1.Story/AddChannelStory', '/bale.story.v1.Story/AddStory', '/bale.story.v1.Story/CanAddBotStory', '/bale.story.v1.Story/CheckLinkValidity', '/bale.story.v1.Story/GetBotStories', '/bale.story.v1.Story/GetChannelStories', '/bale.story.v1.Story/GetMostPopularStories', '/bale.story.v1.Story/GetStories', '/bale.story.v1.Story/GetStoryById', '/bale.story.v1.Story/GetStoryReactionEmojis', '/bale.story.v1.Story/GetStoryTags', '/bale.story.v1.Story/GetStoryWidgets', '/bale.story.v1.Story/GetUserPrivacyConfig', '/bale.story.v1.Story/GetUserStoryConfig', '/bale.story.v1.Story/GetViewers', '/bale.story.v1.Story/GetViewersCount', '/bale.story.v1.Story/ReactToStory', '/bale.story.v1.Story/RemoveStory', '/bale.story.v1.Story/SetUserPrivacyConfig', '/bale.story.v1.Story/SetUserStoryConfig'),
    'bale.timche.v1.Timche': ('/bale.timche.v1.Timche/AskBotReviewCallback', '/bale.timche.v1.Timche/SubmitReview'),
    'bale.top_peer.v1.TopPeer': ('/bale.top_peer.v1.TopPeer/GetTopPeer', '/bale.top_peer.v1.TopPeer/RemovePeer'),
    'bale.turing.v1.AI': ('/bale.turing.v1.AI/GetTranscript', '/bale.turing.v1.AI/SendEvent'),
    'bale.users.v1.Users': ('/bale.users.v1.Users/AddContact', '/bale.users.v1.Users/BlockUser', '/bale.users.v1.Users/ChangeDefaultCardNumber', '/bale.users.v1.Users/ChangePhoneNumber', '/bale.users.v1.Users/CheckNickName', '/bale.users.v1.Users/ConfirmPhoneNumber', '/bale.users.v1.Users/EditAbout', '/bale.users.v1.Users/EditAvatar', '/bale.users.v1.Users/EditName', '/bale.users.v1.Users/EditNickName', '/bale.users.v1.Users/EditUserLocalName', '/bale.users.v1.Users/GetContacts', '/bale.users.v1.Users/GetFullUser', '/bale.users.v1.Users/GetUsersDefaultCardNumber', '/bale.users.v1.Users/ImportContacts', '/bale.users.v1.Users/LoadAvatars', '/bale.users.v1.Users/LoadBlockedUsers', '/bale.users.v1.Users/LoadUsers', '/bale.users.v1.Users/NotifyAboutDeviceInfo', '/bale.users.v1.Users/RemoveAvatar', '/bale.users.v1.Users/RemoveContact', '/bale.users.v1.Users/RemoveDefaultCardNumber', '/bale.users.v1.Users/ResetContacts', '/bale.users.v1.Users/SearchContacts', '/bale.users.v1.Users/UnblockUser'),
    'bale.v1.Configs': ('/bale.v1.Configs/EditParameter', '/bale.v1.Configs/GetInAppUpdate', '/bale.v1.Configs/GetParameters'),
    'bale.v1.Images': ('/bale.v1.Images/AddStickerPack', '/bale.v1.Images/LoadOwnStickers', '/bale.v1.Images/LoadStickerCollection', '/bale.v1.Images/RemoveStickerPack'),
    'bale.wallet.v1.Wallet': ('/bale.wallet.v1.Wallet/GetMyWallets', '/bale.wallet.v1.Wallet/GetPaymentTokenByCard', '/bale.wallet.v1.Wallet/GetWalletChargeToken', '/bale.wallet.v1.Wallet/GetWalletInvoice', '/bale.wallet.v1.Wallet/PayByWallet', '/bale.wallet.v1.Wallet/PayMoneyRequestByWallet', '/bale.wallet.v1.Wallet/VerifyQRCode'),
}


@lru_cache(maxsize=32)
def _filter_paths(service: str | None, query: str | None) -> tuple[str, ...]:
    """Return sorted matching paths (cached)."""
    q = (query or "").lower()
    results: list[str] = []
    for name, methods in SERVICE_PATHS.items():
        if service and service not in name:
            continue
        for path in methods:
            if q and q not in path.lower():
                continue
            results.append(path)
    return tuple(sorted(results))


def list_service_paths(
    service: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> dict[str, object]:
    """Return object-shaped /bale.*/* inventory rows."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    matched = _filter_paths(service, query)
    paths = list(matched[:limit])
    return {
        "paths": paths,
        "count": len(paths),
        "total": len(matched),
        "service": service,
        "query": query,
    }

