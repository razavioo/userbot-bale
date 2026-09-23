# Bale APK RPC inventory (from jadx decompile)

Source: `/home/emad/Downloads/bale.apk` → jadx → `/tmp/opencode/bale-apk/jadx-out/sources/ai/bale/proto`

## Abacus (9)
- EnableShowReactionFlag
- GetMessageReactionsList
- GetMessagesReactions
- GetMessagesViews
- GetShowReactionFlag
- LoadReactions
- MessageReactionsRead
- MessageRemoveReaction
- MessageSetReaction

## Advertisement (132)
- AdReport
- AddComment
- AddCustomIncome
- AddIssue
- BuildAudienceQuery
- CalculatePrice
- CategoryFilter
- ChangeAccountState
- ChangeAdState
- ChangeBonusCodeState
- ChangeCampaignContentState
- ChangeCampaignState
- ChangeChannelIncomeOwner
- ChangeChannelOwnerInfoState
- ChangeChannelShowAdPermissions
- ChangeStatusDialogAdOrder
- ChangeUserAuthDataState
- ChannelIncomeGetCredit
- ChannelIncomePayment
- ConvertIncome
- CreateAd
- CreateAndStartChannelAd
- CreateAutomatedAudience
- CreateBaleDialogCustomAd
- CreateBonusCode
- CreateChannelIncomeFactor
- CreateCustomCampaignPackage
- DeleteCustomIncome
- EditAccount
- EditAdV2
- EditCampaignAd
- EditCampaignContent
- EstimateChannelSponsoredIncome
- FinishAd
- FinishAdV2
- FinishChannelAd
- GetAccountData
- GetAccounts
- GetAccountsByState
- GetActiveAds
- GetActiveChannelAds
- GetAdData
- GetAdDetail
- GetAdProvider
- GetAdReportV2
- GetAdsBySpotAndPlatform
- GetAdsByStateAndSpot
- GetAllChannelIncomesFactor
- GetAllIssues
- GetAllPaymentHistory
- GetAvailableCampaignStartDate
- GetAwaitingToShowAds
- GetAwaitingToShowChannelAds
- GetBaleCustomAd
- GetBonusCodeData
- GetBonusCodes
- GetBusinessAds
- GetCRMIssues
- GetCampaignAds
- GetCampaignContentById
- GetCampaignContents
- GetCampaignData
- GetChannelAds
- GetChannelEarnMoneyInfo
- GetChannelEarnMoneyStatus
- GetChannelGraphReport
- GetChannelIncomeReport
- GetChannelOwnerBankInformation
- GetChannelShowAdCategoryFilter
- GetChannelShowAdPermissions
- GetChannelShowAdTimeRestrict
- GetChannelSponsoredIncomeReport
- GetChannelUndepositedIncomes
- GetChannelsViewReport
- GetConfig
- GetCreditHistory
- GetCreditableAccounts
- GetCustomIncomes
- GetDialogAdOrderDetails
- GetDialogAdOrderPaymentToken
- GetFactorEligibleAds
- GetInvoiceContent
- GetLegalOrgChannels
- GetMyContactPopularChannels
- GetOnBoardingChannels
- GetOnboardingPeers
- GetOnboardingPosts
- GetOnboardingSpotData
- GetOwnerIdByPhone
- GetPaidAdsByTime
- GetPaymentData
- GetPeriodCapacityData
- GetUserAds
- GetUserAuthData
- GetUserCampaigns
- GetUserIssue
- GetUserOnboardingScenario
- GetUserStatus
- GetUsersAuthDataByState
- GetVODContents
- IgnoreUserIssue
- MergeCustomIncomeRecords
- MergeIncreaseCreditRecords
- ModifyCapacity
- RegisterForEarnMoney
- ResolveIssue
- RetryFailedAutoSentInvoice
- SendAdminMessage
- SendFactorMessage
- SendInvoiceForPaymentHistoryRecord
- SendLegalOrgChannelIncome
- SetAdTarget
- SetCapacityMaxViews
- SetChannelInvoiceInfo
- SetChannelOwnerBankInformation
- SetOnBoardingChannels
- SetUserAuthData
- ShowAds
- StartAdV2
- StartBaleCustomAd
- StartChannelAdFromOrder
- StartFromOrder
- StopAllBaleCustomAds
- SubmitChannelAdOrder
- SubmitDialogAdOrder
- SubmitPhotoForBaleCustomAd
- TimeRestrict
- UpdateBusinessAd
- UpdateCRMIssue
- UpdateClick
- UpdateGroupStatus
- UpdateView

## Appzar (3)
- GetMenuButton
- GetMiniAppUrl
- InvokeCustomMethod

## Auth (27)
- ChangeLanguage
- ChangePhone
- DeleteAccount
- DisableTwoFactorAuthentication
- EnableTwoFactorAuthentication
- GetAuthSessions
- GetBajeBamTicket
- GetBaleTicket
- GetJWTToken
- GetTicket
- GetUserIdToken
- IsTwoFactorAuthenticationEnabled
- LogOut
- RecoverPassword
- SendChangePhoneVerificationCode
- SendDeleteAccountVerificationCode
- SetNewPassword
- SignOut
- SignUp
- StartPhoneAuth
- TerminateAllSessions
- TerminateSession
- ValidateCode
- ValidatePassword
- VerifyEmail
- VerifyPassword
- VerifyPasswordRecovery

## Bank (17)
- BuyFastCharge
- GetCardRemain
- GetCardTransferToken
- GetOTPToken
- GetOTPTokenV2
- GetOrganizationPaymentToken
- GetPSProxyPaymentToken
- GetPSProxyToken
- GetPayMoneyRequestToken
- GetPaymentToken
- GetPayvandCard
- GetPayvandCardList
- GetRecentCharges
- GetRemainToken
- GetSadadPSPPaymentToken
- GrantBankiAccess
- InvoiceGetToken

## BankAccountPreferences (3)
- ActivateYaraMessaging
- EditPreference
- GetPreferences

## Bill (8)
- CreateSavedBill
- DeleteSavedBills
- GetBillHistory
- GetBillMenu
- GetSavedBills
- InquiryBill
- PayBill
- RenameSavedBill

## Business (16)
- CreateBusinessBot
- CreateRequest
- EditBusinessBot
- GetBusinessInvoice
- GetBusinessInvoices
- GetBusinessTransactions
- GetChargeCreditPaymentToken
- GetOrganizationInfo
- GetSpecificRequests
- IsValidNationalCode
- Login
- Logout
- SetRequestSupport
- UpdateRequestInfo
- UpdateRequestStatus
- ValidateOtp

## BusinessAdmin (12)
- ChargeBusinessWallet
- CreateManualPayment
- EditOrganization
- EditRequest
- GetBusinessAdminMessageHistory
- GetManualPaymentsReport
- GetOrganization
- GetOrganizations
- GetPaymentGatewayReport
- GetRequests
- SendBusinessAdminMessage
- ValidateNationalCode

## Club (7)
- BuyVoucher
- BuyWheelChance
- ChangePointToMoney
- GetAcquiredVouchers
- GetVouchers
- GetWheel
- SpinWheel

## Configs (3)
- EditParameter
- GetInAppUpdate
- GetParameters

## CouponCredit (1)
- GetAvailableGroupCoupons

## Dake (6)
- GetFavoriteCategory
- GetNewsByCategory
- GetNewsCoverage
- GetNewsReaction
- SetFavoriteCategory
- SetNewsReaction

## Enrichment (1)
- GetLinkPreview

## Evex (8)
- GetEvexCurrenciesList
- GetEvexCurrencyPrice
- GetEvexPaymentToken
- GetListOfEvexDeliveryStations
- GetValidBanks
- LoadEvexHistory
- VerifyUserEvexAuthority
- VerifyUserEvexExtraInfo

## Exchange (10)
- GetCurrenciesList
- GetCurrencyPrice
- GetExchangeOrderInfo
- GetExchangePaymentToken
- GetInitialConfig
- GetListOfDeliveryStations
- GetTravelCurrencyOrderDetail
- GetUserIcmsInfo
- LoadExchangingHistory
- VerifyUserExchangeAuthority

## Falake (1)
- GetLinkStatus

## Fanoos (1)
- SendFanoos

## Files (7)
- FileUploadCancel
- GetNasimFilePublicUrl
- GetNasimFileUploadResume
- GetNasimFileUploadUrl
- GetNasimFileUrl
- GetNasimFileUrls
- GetUploadLimits

## Garson (11)
- EditCustomServices
- GetAdvertisementBot
- GetBotBanners
- GetBotsByCategory
- GetCategorizedBots
- GetCustomServices
- GetRecommendedBots
- GetServices
- GetTrendBots
- GetUserRepeatedBots
- SearchServices

## Gold (5)
- BuyGold
- GetGoldUnitPrice
- GetGoldWalletBalance
- GoldAuthentication
- SellGold

## Groups (51)
- AddDiscussionGroupAdmin
- CreateGroup
- EditChannelNick
- EditGroupAbout
- EditGroupAvatar
- EditGroupDefaultCardNumber
- EditGroupTitle
- FetchGroupAdmins
- GetBannedUsers
- GetCanSeeMessages
- GetFullGroup
- GetGroupDefaultCardNumber
- GetGroupInviteUrl
- GetGroupMembersCount
- GetGroupPreview
- GetGroupRecommendations
- GetMemberPermissions
- GetMutualGroups
- GetMyGroups
- GetPins
- GetPublicGroups
- InviteUser
- InviteUsers
- JoinGroup
- JoinPublicGroup
- KickUser
- LeaveGroup
- LoadFullGroups
- LoadGroupAvatars
- LoadGroups
- LoadMembers
- MakeUserAdmin
- PinMessage
- RemoveDiscussionGroup
- RemoveGroupAvatar
- RemovePin
- RemoveSingePin
- RemoveUserAdmin
- RevokeInviteUrl
- SetAvailableReactions
- SetCanSeeHistory
- SetCanSeeMessages
- SetDiscussionGroup
- SetGroupDefaultPermissions
- SetMemberCustomTitle
- SetMemberPermissions
- SetRestriction
- SetSignMessages
- SetSlowMode
- TransferOwnership
- UnBanUser

## Images (10)
- AddGif
- AddStickerCollection
- AddStickerPack
- GetSavedGifs
- LoadOwnStickers
- LoadStickerCollection
- RemoveGif
- RemoveStickerCollection
- RemoveStickerPack
- UseGif

## Ketf (14)
- GetBotGroupPermissions
- GetBotInfo
- GetBotWhiteList
- GetBots
- GetInlineBotResults
- GetPaymentDetails
- GetUserContext
- GetWebappHash
- InvokeCustomAction
- MakePayment
- SendAuthenticatedInlineCallBackData
- SendInlineCallBackData
- SendInlineCallback
- SendMiniAppData

## Kifpool (30)
- CashOutKifpool
- Charge
- CheckChargePermission
- CreateKifpool
- CryptoCashOutKifpool
- CryptoInvoice
- CryptoPurchase
- CryptoRefund
- CryptoTransfer
- FeeInquiry
- GetChargePaymentToken
- GetCredit
- GetCryptoChargePaymentToken
- GetCryptoWallets
- GetKifpoolOwner
- GetMyKifpools
- GetPointBalance
- GetPointDetails
- GetPointSummery
- InvoiceKifpool
- PayForMessage
- Purchase
- PurchaseMessage
- PurchaseMessageWithCharge
- PurchaseWithCharge
- TransactionPoint
- TransferMoney
- UpgradeKifpool
- VerifyCashOutKifpool
- VerifyPurchaseMessage

## Lahze (6)
- CreateLive
- EditLiveMainMessage
- EndLive
- GetGroupsLives
- GetLiveInfo
- StartLive

## Magazine (8)
- GetMessageUpvoters
- GetMyUpvotes
- GetSimilarPosts
- LoadCategories
- LoadCategoryFeedMessages
- LoadFeedMessages
- RevokeUpvotedPost
- UpvotePost

## Market (25)
- AcceptCampaignMarket
- AcceptMarketJoinRequest
- CreateMarketJoinRequest
- CreateTag
- GetCategories
- GetCategoryMarkets
- GetCategoryProducts
- GetIndexedProducts
- GetMarket
- GetMarketJoinRequests
- GetMarketsJoinRequest
- GetNumberOfSales
- GetOnboardingStatus
- GetPendingCampaignMarkets
- GetStores
- GetTags
- GetTopMarkets
- RejectCampaignMarket
- RejectMarketJoinRequest
- SetGenericDeepLinks
- SetMarketBanners
- SetOnboardingData
- SetPopularSearches
- SubmitMarketFeedback
- UpdateMarketInfo

## MavizStream (4)
- GetDifference
- SubscribeToThreadUpdates
- SubscribeToUpdates
- UnsubscribeFromThreadUpdates

## Meet (32)
- AcceptCall
- AnswerCallJoinRequest
- AskToJoinCall
- DeleteCallLogs
- DeleteStream
- DiscardCall
- GenerateCallLink
- GetCallLinkDetails
- GetCallLogs
- GetCallState
- GetGroupCall
- GetOngoingCalls
- GetWssURL
- InviteToCall
- JoinGroupCall
- LeaveGroupCall
- MuteParticipant
- ReceiveCall
- RemoveParticipant
- SendReaction
- SetLinkTitle
- StartCall
- StartGroupCall
- StartInternalCall
- StartLiveKitCall
- StartRecording
- StartSipCall
- StartStream
- StopRecording
- SubmitCallFeedback
- TakeCallAction
- UpdateLayout

## Melon (5)
- GetLoanInfo
- GetLoansList
- LoadLastStates
- LoadLoanHistory
- RemoveLoan

## MessageStream (2)
- CancelMessageStream
- ReceiveMessageStream

## Messaging (44)
- ArchiveDialogs
- ClearChat
- CreateFolder
- CreateReservedFolder
- CreateThread
- CreateTopic
- DeleteChat
- DeleteFolder
- DeleteMessage
- DeleteTopic
- EditFolder
- EditTopic
- FetchProtectedMessage
- ForwardMessages
- GetDiscussionMessage
- GetMessagesRepliesInfo
- GetTopicByID
- GetTopics
- LoadArchived
- LoadDialogs
- LoadFolderDialogs
- LoadFolders
- LoadGroupedDialogs
- LoadHistory
- LoadPeerDialogs
- LoadPeers
- LoadPinnedDialogs
- LoadPinnedMessages
- LoadReplies
- MarkDialogsAsRead
- MarkDialogsAsUnread
- MentionRead
- MessageRead
- MessageReceived
- PinDialogs
- PinMessages
- ReorderFolders
- ReorderPinnedDialogs
- SendMessage
- SendMultiMediaMessage
- UnArchiveDialogs
- UnPinMessages
- UnpinDialogs
- UpdateMessage

## MyBank (1)
- GetMyBank

## Negah (1)
- GetMessageSeenList

## Omre (10)
- GetInitialOmreConfig
- GetListOfOmreDeliveryStations
- GetOmreCurrenciesList
- GetOmreCurrencyOrderDetail
- GetOmreCurrencyPrice
- GetOmreOrderInfo
- GetOmrePaymentToken
- GetUserOmreIcmsInfo
- LoadOmreHistory
- VerifyUserOmreAuthority

## Organizations (2)
- GetUserOrganizationInfo
- GetUserOrganizationalContacts

## Passport (11)
- GetPassportGroup
- GetPassportGroupList
- GetPassportInfo
- HasPassportInfo
- LoadFormLink
- RemovePassportInfo
- SetPassportGroup
- SetPassportInfo
- SetPassportInfoByLink
- SubmitForm
- ValidateField

## Pfm (15)
- AddDetailToTransaction
- AddTransactionTags
- AddUserTags
- FilterTaggedTransactions
- GetSubTransactions
- GetTransactionTags
- GetUserAccounts
- GetUserTags
- LoadTransactions
- LoadTransactionsByIDs
- RemoveTransaction
- RemoveTransactionTags
- RemoveUserTags
- ReviveTransaction
- SplitTransaction

## Pishvaz (3)
- GetMarketingToolsConfig
- GetOnboardingPageData
- SetMarketingToolAction

## PishvazAdmin (18)
- CreateAudience
- CreateCampaign
- CreateJob
- DeleteCampaigns
- DeleteJobs
- EditCampaign
- EditJob
- GenerateQuery
- GetAudience
- GetAudiences
- GetCampaign
- GetCampaignHistories
- GetCampaignHistory
- GetCampaigns
- GetJob
- GetJobs
- RollBackCampaign
- UpdateAudiences

## Poll (5)
- ClosePoll
- CreatePoll
- GetFullPollResult
- GetPollResults
- Vote

## Premium (7)
- CalculateDiscountedPrice
- GetBadges
- GetPackages
- IsPremium
- IsPremiumBatch
- PurchasePackage
- SetUserBadge

## Presence (11)
- GetContactsPresences
- GetGroupMembersPresence
- GetGroupOnlineCount
- GetUsersPresence
- SetOnline
- StopTyping
- SubscribeFromGroupOnline
- SubscribeFromOnline
- SubscribeToGroupOnline
- SubscribeToOnline
- Typing

## Push (6)
- RegisterGooglePush
- RegisterPush
- SetConfig
- UnregisterAllPushCredentials
- UnregisterGooglePush
- UnregisterPush

## Ramz (7)
- CheckPassword
- CheckPasswordSet
- DeletePassword
- ForgetPassword
- SendOtp
- SetPassword
- ValidateOTP

## Reactions (4)
- BindMoneyRequestDetails
- BindMoneyRequestDetailsList
- GetReactions
- UnbindAllMoneyRequestDetails

## Recommender (4)
- GetChannelRecommendations
- GetGroupsRecommendation
- GetRelatedChannels
- GetRelatedGroups

## Referral (4)
- GetReferralCode
- GetReferredCount
- GetReferringUser
- Refer

## Report (2)
- ReportDismiss
- ReportInappropriateContent

## Safir (7)
- EditSafirTemplateMessage
- EditSafirs
- GetOrganizationOwner
- GetSafirTemplateMessages
- GetSafirs
- GetSendMessageReports
- RevokeAPIKey

## SafirAdmin (5)
- DeleteSafirBotAdmin
- EditAdminSafirTemplateMessages
- ExpireSafirMessages
- RevokeAPIKeyAdmin
- SendMessageByAudienceGroup

## SafirPanelMessage (3)
- CreateMessageOrder
- GetMessageOrders
- VerifySendMessage

## Sap (16)
- AddDestinationCards
- AddNewCards
- DeliverOtp
- EditCardExpirationDate
- EnrollNewCard
- GetCardInfo
- GetCards
- GetDefaultCard
- GetDestinationCardInfo
- GetDestinationCards
- ReactivateApp
- RemoveCard
- RemoveDefaultCard
- RemoveDestinationCards
- SetDefaultCard
- TransferMoneyByCard

## Sarrafi (9)
- AuthenticateUser
- CreateSarrafiOrder
- GetChargeToken
- GetDepth
- GetSarrafiOrder
- GetSarrafiOrders
- GetSession
- GetTickers
- GetWallet

## Scheduler (6)
- ExecuteTaskNow
- ListTasks
- PeersWithScheduleTask
- ReScheduleTask
- ScheduleTask
- UnScheduleTask

## Search (12)
- RecommendPeer
- SearchContent
- SearchDialog
- SearchMarket
- SearchMarketPopular
- SearchMedia
- SearchMembers
- SearchMessages
- SearchMessagesMore
- SearchPeer
- SearchProduct
- UpdateSearchContentClick

## Sefte (12)
- AddRecipient
- AuthorizeUser
- CheckStatusOfPayment
- GetCitiesOfState
- GetRecipients
- GetStates
- GetUserSeftes
- InitP12
- InitSefte
- PaySefte
- SignSefte
- UserHasCred

## Sentence (5)
- GetMyAwardsSummary
- GetMySentence
- GetMyTransactionsSummary
- SendMyGiftPacket
- ValidateMySentence

## SharedMedia (1)
- LoadMedia

## Story (24)
- AddBotStory
- AddChannelStory
- AddStory
- CanAddBotStory
- CheckLinkValidity
- GetAllStories
- GetBotStories
- GetChannelStories
- GetDefaultStoryBackgrounds
- GetMostPopularStories
- GetStories
- GetStoriesByList
- GetStoryById
- GetStoryReactionEmojis
- GetStoryTags
- GetStoryWidgets
- GetUserPrivacyConfig
- GetUserStoryConfig
- GetViewers
- GetViewersCount
- ReactToStory
- RemoveStory
- SetUserPrivacyConfig
- SetUserStoryConfig

## Ticket (8)
- CloseTicket
- CreateTicketArticle
- GetRatingOptions
- GetTicketCategories
- GetTickets
- LoadFullTicket
- SubmitRating
- SubmitTicket

## Timche (5)
- AskBotReviewCallback
- GetBotPage
- GetHomePage
- GetSectionPage
- SubmitReview

## TopPeer (2)
- GetTopPeer
- RemovePeer

## Users (35)
- AddCard
- AddContact
- BlockUser
- ChangeDefaultCardNumber
- ChangePhoneNumber
- CheckNickName
- ConfirmPhoneNumber
- EditAbout
- EditAvatar
- EditBirthDate
- EditMyPreferredLanguages
- EditMyTimeZone
- EditName
- EditNickName
- EditSex
- EditUserLocalName
- GetContacts
- GetFullUser
- GetUserFullPrivacy
- GetUserPrivacyStatus
- GetUsersDefaultCardNumber
- ImportContacts
- IsNameAllowed
- LoadAvatars
- LoadBlockedUsers
- LoadFullUsers
- LoadUsers
- NotifyAboutDeviceInfo
- RemoveAvatar
- RemoveContact
- RemoveDefaultCardNumber
- ResetContacts
- SearchContacts
- SetUserPrivacyStatus
- UnblockUser

## Vitrine (5)
- AddItemToVitrinePanel
- GetFullVitrine
- GetUserBank
- GetUserVitrine
- RequisitionEnterToVitrine

## Wallet (13)
- ActivateWallet
- CashOutFromWallet
- GetMoneyRequestPaymentTokenByCard
- GetMyWallets
- GetPaymentTokenByCard
- GetWalletChargeToken
- GetWalletContracts
- GetWalletInvoice
- PayByWallet
- PayMoneyRequestByWallet
- VerifyCashOut
- VerifyPeer
- VerifyQRCode

## Warrior (8)
- AcceptReferralInvite
- GetPacket
- GetPacketsStatus
- GetReferralContacts
- GetScoreBoard
- GetUserRefers
- GetUserScore
- SendReferral

