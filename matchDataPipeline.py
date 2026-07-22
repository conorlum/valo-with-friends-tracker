import time
import json
import os
import shutil
from PIL import Image
import imagehash
import glob
import shutil
from pathlib import Path
import networkx as nx
import re

User = "User" #conor or User
resolutionScaling = 1 #1.5 when on 4k
baseLocation = f"C:\\Users\\{User}\\Documents\\GitHub\\ValorantIGLTutor\\MatchPages\\"



def createMapFolder(filename):
	path = os.path.join(baseLocation, filename + "_ALL_FILES")

	# Create the folder
	os.makedirs(path, exist_ok=True)

def clickAndSaveRound(roundCount, filenamePrefix):
	import pyautogui
	pyautogui.click()
	pyautogui.keyDown('ctrl')
	pyautogui.press('s')
	pyautogui.keyUp('ctrl')

	time.sleep(4)

	pyautogui.press('backspace')
	time.sleep(1)
	filename = filenamePrefix + f"-Round{roundCount}"
	pyautogui.write(filename)
	time.sleep(1)
	pyautogui.press("tab")
	time.sleep(1)
	pyautogui.press("down", presses=3, interval = .5)
	time.sleep(1)
	pyautogui.press('enter')
	time.sleep(1)
	pyautogui.press('enter')
	time.sleep(5)

	while checkFileExists(filenamePrefix, filename) is False:
		print("waiting")
		time.sleep(10)

def checkHTMLExists(filenamePrefix, filename):
	path = f"{baseLocation}{filenamePrefix}_ALL_FILES\\"
	pattern = os.path.join(path, f"*{filename}.html")
	print(pattern)
	print(glob.glob(pattern))
	return bool(glob.glob(pattern))

def checkFileExists(filenamePrefix, filename):
	path = f"{baseLocation}{filenamePrefix}_ALL_FILES\\"
	pattern = os.path.join(path, f"*{filename}*")
	print(pattern)
	print(glob.glob(pattern))
	return bool(glob.glob(pattern))


def moveOneRoundOver():
	import pyautogui
	pyautogui.moveRel(64 * resolutionScaling,0)

def parseOutRoundCount(filename):
	html = ""

	startHTML = False
	with open(f"{baseLocation}{filename}_ALL_FILES\\{filename}.html", "r", encoding="utf-8") as file:
		for line in file:

			if startHTML and line.startswith("<script>window.__INITIAL_STATE__"):
				break

			if startHTML:
				html += line.rstrip()

			if "<body" in line:
				startHTML = True

	teamARounds = int(html.split("Team A")[1].split('valorant-color-team-1">')[1].split("<")[0])
	teamBRounds = int(html.split("Team B")[1].split('valorant-color-team-2">')[1].split("<")[0])
	return teamARounds + teamBRounds

def saveAllRounds(filename):
	roundCountTotal = parseOutRoundCount(filename)
	print(roundCountTotal)
	TotalRoundIndex = 1
	while roundCountTotal > 0:
		if roundCountTotal >= 20:
			roundIndexTotal = 20
			roundCountTotal -= 20
		else:
			roundIndexTotal = roundCountTotal
			roundCountTotal = 0

		print("Move mouse onto start loop round")
		input()

		print("looping for: " + str(roundIndexTotal))
		for roundIndex in range(0,roundIndexTotal):
			clickAndSaveRound(roundIndex + TotalRoundIndex, filename)
			moveOneRoundOver()

		TotalRoundIndex += roundIndexTotal


def cleanUpFiles(filename):

	path = os.path.join(baseLocation, filename + "_ALL_FILES")

	# Create the folder
	os.makedirs(path, exist_ok=True)

	files = glob.glob(f"{baseLocation}{filename}*")

	prefix = Path(path + "\\")
	
	for file in files:
		if "ALL_FILES" in file:
			continue
		dst = str(prefix) +"\\" + file.split("MatchPages\\")[1]
		shutil.move(str(file), str(dst))



def saveHTMLToJson(filename):
	roundCountTotal = parseOutRoundCount(filename)
	roundHTMLJson = {}
	for i in range(0, roundCountTotal):
		roundIndexStr = str(i+1)
		filePath = f"{baseLocation}{filename}_ALL_FILES\\{filename}-Round{roundIndexStr}.html"
		with open(filePath, 'r', encoding="utf-8") as file:
			startHTML = False
			html = ""
			for line in file:
				if startHTML and line.startswith("<script>window.__INITIAL_STATE__"):
					break

				if startHTML:
					html += line.rstrip()

				if "<body" in line:
					startHTML = True

			roundHTMLJson[str(i+1)] = html

	filePath = f"C:\\Users\\{User}\\Documents\\GitHub\\ValorantIGLTutor\\MatchHTMLJsons\\{filename}.json"
	with open(filePath, 'w') as file:
		json.dump(roundHTMLJson, file, indent=4)


def loadHTMLSFromJson(filename):
	htmls = {}
	with open(f"C:\\Users\\{User}\\Documents\\GitHub\\ValorantIGLTutor\\MatchHTMLJsons\\{filename}.json", 'r') as file:
		htmls = json.load(file)

	return htmls

def parseEconPerRound(filename):
	htmls = loadHTMLSFromJson(filename)

	roundEcons = {}

	for roundIndex in htmls.keys():
		html = htmls[str(roundIndex)]
		roundMarker = html.split(f"text-12 font-medium text-dim\">{roundIndex}<")[1]

		econTeam1Split = roundMarker.split('"w-3 bg-valorant-team-1\" style=\"height: ')[1]
		team1EconString = econTeam1Split.split("px")[0]
		team1EconAdjusted = round(float(team1EconString) * .625 * 1000)



		econTeam2Split = roundMarker.split('"w-3 bg-valorant-team-2\" style=\"height: ')[1]
		team2EconString = econTeam2Split.split("px")[0]
		team2EconAdjusted = round(float(team2EconString) * .625 * 1000)

		roundEcons[roundIndex] = {"Team1" : team1EconAdjusted, "Team2" : team2EconAdjusted}
		

	return roundEcons

def parseRoundOutcome(filename):
	htmls = loadHTMLSFromJson(filename)

	roundOutcomes = {}

	for roundIndex in htmls.keys():
		html = htmls[str(roundIndex)]
		pattern = r"Team (A|B) (.{1,20}) Win"
		roundOutcome = re.search(pattern, html).group(0)
		roundOutcomes[str(roundIndex)] = roundOutcome

	return roundOutcomes

def agentDisplayIconLookup(displayiconNumber, roundIndex, filename):

	png_files = glob.glob(f"C:\\Users\\{User}\\Documents\\GitHub\\ValorantIGLTutor\\agentIconReferences\\*.png")
	hash1 = imagehash.average_hash(Image.open(f"{baseLocation}{filename}_ALL_FILES\\{filename}-Round{roundIndex}_files\\displayicon{displayiconNumber}.png"))
	best_png = ""
	best_hash = 2147483647

	for png in png_files:
	
		# print(png)
		hash2 = imagehash.average_hash(Image.open(png))

		distance = abs(hash1 - hash2)
		if distance < best_hash:
			best_hash = distance
			best_png = png

	if best_hash > 5:
		return "BAD CLASSIFICATION!"

	if best_png.split("agentIconReferences\\")[1].split(".png")[0] == "KAYO":
		return "KAY/O"

	return best_png.split("agentIconReferences\\")[1].split(".png")[0]

def weaponNewImageLookup(newImageNumber, roundIndex, filename):

	png_files = glob.glob("C:\\Users\\{User}\\Documents\\GitHub\\ValorantIGLTutor\\weaponIconReferences\\*.png")
	hash1 = imagehash.average_hash(Image.open(f"{baseLocation}{filename}_ALL_FILES\\{filename}-Round{roundIndex}_files\\newimage{newImageNumber}.png"))
	best_png = ""
	best_hash = 2147483647

	for png in png_files:
	
		# print(png)
		hash2 = imagehash.average_hash(Image.open(png))

		distance = abs(hash1 - hash2)
		if distance < best_hash:
			best_hash = distance
			best_png = png

	if best_hash > 5:

		return "BAD CLASSIFICATION!"
	return best_png.split("weaponIconReferences\\")[1].split(".png")[0]


def convertTime(stringTime):
	return int(stringTime[0])*60 + int(stringTime[2:])


def parseRoundKillList(filename):
	htmls = loadHTMLSFromJson(filename)

	# figure this out 
	roundKillLogs = {}

	for roundIndex in htmls.keys():
		html = htmls[str(roundIndex)]

		roundMarker = html.split("Event Log")[1]
		logMarkers = roundMarker.split("space-between flex cursor-pointer flex-row items-center")

		logMarkers = logMarkers[1:]

		killLog = []

		team1ACSBonus = 150
		team2ACSBonus = 150

		for logMarker in logMarkers:

			eventTime = convertTime(logMarker.split("text-16 font-medium leading-3/4\">")[1].split("<")[0])


			if "Planted" not in logMarker and "Exploded" not in logMarker and "Defused" not in logMarker:


				classMarkers = logMarker.split("class")
				killerTeam = classMarkers[1].split("valorant-")[1][:6]

				killerCharacter = classMarkers[2].split("displayicon")[1].split(".png")[0]

				try:
					deathTeam = classMarkers[-3].split("valorant-")[1][:6]
					deathCharacter = classMarkers[-2].split("displayicon")[1].split(".png")[0]
				except Exception as e:
					try:
						deathTeam = classMarkers[8].split("valorant-")[1][:6]
						deathCharacter = classMarkers[9].split("displayicon")[1].split(".png")[0]
					except Exception as e:
						try:
							deathTeam = classMarkers[7].split("valorant-")[1][:6]
							deathCharacter = classMarkers[8].split("displayicon")[1].split(".png")[0]
						except Exception as e:
							print(classMarkers)
							print(e)


				killerCharacter = agentDisplayIconLookup(killerCharacter, roundIndex, filename)
				deathCharacter = agentDisplayIconLookup(deathCharacter, roundIndex, filename)

				if killerTeam == deathTeam:
					killWeapon = "Friendly"
				elif "newimage" not in logMarker:
					killWeapon = "Environmental"
				else:
					killWeapon = logMarker.split("newimage")[1].split(".png")[0]
					killWeapon = weaponNewImageLookup(killWeapon, roundIndex, filename)

				

				ACSBonus = team1ACSBonus if killerTeam == "team-1" else team2ACSBonus

				killLog.append({"killerTeam" : killerTeam, "killerCharacter" : killerCharacter, "deathTeam" : deathTeam, "deathCharacter" : deathCharacter, "killWeapon" : killWeapon, "eventTime" : eventTime, "Event" : "Kill", "ACS_Bonus" : ACSBonus})

				if killerTeam == "team-1":
					team1ACSBonus -= 20
				else:
					team2ACSBonus -= 20
			else:
				team = classMarkers[1].split("valorant-")[1][:6]
				character = classMarkers[2].split("displayicon")[1].split(".png")[0]
				character = 0 if character == 'p' else character
				character = agentDisplayIconLookup(character, roundIndex, filename)

			if "Planted" in logMarker:
				killLog.append({"Team" : team, "Character" : character, "Event" : "Planted", "eventTime" : eventTime})

			if "Exploded" in logMarker:
				killLog.append({"Team" : team, "Character" : character, "Event" : "Exploded", "eventTime" : eventTime})

			if "Defused" in logMarker:
				killLog.append({"Team" : team, "Character" : character, "Event" : "Defused", "eventTime" : eventTime})



		roundKillLogs[roundIndex] = killLog

	return roundKillLogs


def parseTeamPlayers(filename):
	htmls = loadHTMLSFromJson(filename)
	round1 = htmls["1"]
	playerLogRecords = round1.split("st-custom-name st-entry-party st-entry")
	playerUsernamesToAgent = {}

	teamCount = 0
	team = "team-1"

	for playerLogIndex in range(0,10):
		playerLogRecord = playerLogRecords[playerLogIndex+1]
		playerAgent = playerLogRecord.split("alt=\"")[1].split("\"")[0]

		username = playerLogRecord.split("trn-ign__username fit-long-username\">")[1].split("<")[0]


		playerUsernamesToAgent[username] = {"Agent" : playerAgent, "Team" : team, "RoundInfo" : []}

		teamCount += 1
		if teamCount == 5:
			team = "team-2"

	return playerUsernamesToAgent
	

def parsePlayerRoundInfo(filename):
	htmls = loadHTMLSFromJson(filename)

	playerUsernamesToAgent = parseTeamPlayers(filename)

	for roundIndex in htmls.keys():
		html = htmls[str(roundIndex)]
		players = html.split("trn-ign__username fit-long-username")

		
		for player in players:
			if "st__item st-content__item-value st-content__item-value--active st__item--align-center" in player:
				username = (player[2:].split("<")[0])
				commaScore = player.split("st__item st-content__item-value st-content__item-value--active st__item--align-center")[1].split("value\">")[1].split("<")[0]
				score = int(commaScore.replace(",", ""))
				kills = int(player.split("st__item st-content__item-value st-content__item-value--active st__item--align-center")[1].split("value\">")[2].split("<")[0])
				deaths = int(player.split("st__item st-content__item-value st-content__item-value--active st__item--align-center")[1].split("value\">")[3].split("<")[0])
				assists = int(player.split("st__item st-content__item-value st-content__item-value--active st__item--align-center")[1].split("value\">")[4].split("<")[0])
				commaLoadout= player.split("st__item st-content__item-value st-content__item-value--active st__item--align-center")[1].split("value\">")[5].split("<")[0]
				commaRemaining = player.split("st__item st-content__item-value st-content__item-value--active st__item--align-center")[1].split("value\">")[5].split("label\">")[1].split("<")[0]
				loadout = int(commaLoadout.replace(",", ""))
				remaining = int(commaRemaining.replace(",", ""))

				roundPlayerData = {}
				roundPlayerData["Score"] = score
				roundPlayerData["Kills"] = kills
				roundPlayerData["Deaths"] = deaths
				roundPlayerData["Assists"] = assists
				roundPlayerData["Loadout"] = loadout
				roundPlayerData["Remaining"] = remaining
				playerUsernamesToAgent[username]["RoundInfo"].append(roundPlayerData)

	return playerUsernamesToAgent

def displayImpact(playersRoundInfo, roundInfoBool):
	sortedByImpact = {}
	playerID = 0
	for username in playersRoundInfo.keys():
		displayString = []
		player = playersRoundInfo[username]
		agent = player["Agent"]
		team = player["Team"]

		displayString.append("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
		displayString.append(username)
		displayString.append(agent)
		displayString.append(team)
		displayString.append("\n")

		avgDeathImpact = 0
		avgKillImpact = 0
		avgImpact = 0
		avgACS = 0
		for roundIndex in range(0,len(player["RoundInfo"])):
			if roundInfoBool:
				displayString.append("Round " + str(roundIndex+1))
				displayString.append(player["RoundInfo"][roundIndex]["ImpactDisplay"])
				displayString.append("ACS: " + str(player["RoundInfo"][roundIndex]["Score"]))
				displayString.append("\n")
			avgKillImpact += player["RoundInfo"][roundIndex]["killImpact"]
			avgDeathImpact += player["RoundInfo"][roundIndex]["deathImpact"]
			avgImpact += player["RoundInfo"][roundIndex]["Impact"]
			avgACS += player["RoundInfo"][roundIndex]["Score"]

		displayString.append("Average Kill Impact: " + str(round(avgKillImpact/len(player["RoundInfo"]))))
		displayString.append("Average Death Impact: " + str(round(avgDeathImpact/len(player["RoundInfo"]))))
		displayString.append("Average Impact: " + str(round(avgImpact/len(player["RoundInfo"]))))
		displayString.append("Average ACS: " + str(round(avgACS/len(player["RoundInfo"]))))
		displayString.append("\n")

		result = "\n".join(displayString)
		sortedByImpact[str(round(avgImpact/len(player["RoundInfo"]))) + str(playerID)] = result
		playerID += 1

	intKeys = []
	for key in sortedByImpact.keys():
		intKeys.append(int(key))

	intKeys.sort(reverse=True)
	for key in intKeys:
		print(sortedByImpact[str(key)])


def calculateRoundImpact(playersRoundInfo):

	for username in playersRoundInfo.keys():
		player = playersRoundInfo[username]
		agent = player["Agent"]
		team = player["Team"]

		for roundIndex in range(0,len(player["RoundInfo"])):
			ACS = player["RoundInfo"][roundIndex]["Damage+Assists"]
			killOrderBonus = player["RoundInfo"][roundIndex]["killOrderBonusSum"]

			killOrderBonusXEconFactorSum = player["RoundInfo"][roundIndex]["killOrderBonus*EconFactorSum"]
			deathOrderBonusXEconFactorSum = player["RoundInfo"][roundIndex]["deathOrderBonus*EconFactorSum"]

			killOrderBonusXTimeFactorSum = player["RoundInfo"][roundIndex]["killOrderBonus*TimeFactorSum"]
			deathOrderBonusXTimeFactorSum = player["RoundInfo"][roundIndex]["deathOrderBonus*TimeFactorSum"]

			killOrderBonusXEconSwingRiskFactorSum = player["RoundInfo"][roundIndex]["killOrderBonus*EconSwingRiskFactorSum"]
			deathOrderBonusXEconSwingRiskFactorSum = player["RoundInfo"][roundIndex]["deathOrderBonus*EconSwingRiskFactorSum"]

			ACS_Scalor = 1.25
			damages = round(ACS*ACS_Scalor)
			killImpact = round(damages + ((killOrderBonusXEconFactorSum + killOrderBonusXTimeFactorSum + killOrderBonusXEconSwingRiskFactorSum) / 3))

			deathImpact = round((deathOrderBonusXEconFactorSum + deathOrderBonusXTimeFactorSum + deathOrderBonusXEconSwingRiskFactorSum) / 3)
			player["RoundInfo"][roundIndex]["killImpact"] = killImpact
			player["RoundInfo"][roundIndex]["deathImpact"] = deathImpact

			impact = killImpact - deathImpact
			player["RoundInfo"][roundIndex]["Impact"] = impact
			player["RoundInfo"][roundIndex]["ImpactDisplay"] = "Impact: " + str(round(impact)) + "     Breakdown  -->     killImpact: " + str(killImpact) +  "  Damage: " + str(damages) + "   Econ Impact: " + str(round(killOrderBonusXEconFactorSum - deathOrderBonusXEconFactorSum)) + "    Time Impact: " + str(round(killOrderBonusXTimeFactorSum - deathOrderBonusXTimeFactorSum)) + "    Swing Round Impact: " + str(round(killOrderBonusXEconSwingRiskFactorSum - deathOrderBonusXEconSwingRiskFactorSum))

	return playersRoundInfo


def calculateDamageAndAssists_KillOrderSum_KillFactorAverage(playersRoundInfo, roundKillLogs):

	for username in playersRoundInfo.keys():
		player = playersRoundInfo[username]
		agent = player["Agent"]
		team = player["Team"]

		for roundIndex in range(0,len(player["RoundInfo"])):
			ACS = player["RoundInfo"][roundIndex]["Score"]
			killOrderBonus = 0
			killOrderBonusXEconFactorSum = 0
			killOrderBonusXTimeFactorSum = 0
			killOrderBonusXEconSwingRiskFactor = 0
			killsInRound = 0

			for killLog in roundKillLogs[str(roundIndex+1)]:
				
				if killLog["Event"] == "Kill":
					if killLog["killerTeam"] == team and killLog["killerCharacter"] == agent:
						ACS -= killLog["ACS_Bonus"]
						killOrderBonus += killLog["killOrderBonus"]
						killsInRound += 1
						killOrderBonusXEconFactorSum += killLog["killOrderBonus*EconFactor"]
						killOrderBonusXTimeFactorSum += killLog["killOrderBonus*TimeFactor"]
						killOrderBonusXEconSwingRiskFactor += killLog["killOrderBonus*EconSwingRiskFactor"]

			adjustACSForMultikill = 0
			if killsInRound > 1:
				adjustACSForMultikill = -50 * killsInRound

			player["RoundInfo"][roundIndex]["Damage+Assists"] = ACS - adjustACSForMultikill
			player["RoundInfo"][roundIndex]["killOrderBonusSum"] = killOrderBonus
			player["RoundInfo"][roundIndex]["killOrderBonus*EconFactorSum"] = killOrderBonusXEconFactorSum
			player["RoundInfo"][roundIndex]["killOrderBonus*TimeFactorSum"] = killOrderBonusXTimeFactorSum
			player["RoundInfo"][roundIndex]["killOrderBonus*EconSwingRiskFactorSum"] = killOrderBonusXEconSwingRiskFactor

		for roundIndex in range(0,len(player["RoundInfo"])):
			deathOrderBonusXEconFactorSum = 0
			deathOrderBonusXTimeFactorSum = 0
			deathOrderBonusXEconSwingRiskFactor = 0

			for killLog in roundKillLogs[str(roundIndex+1)]:
				
				if killLog["Event"] == "Kill":
					if killLog["deathTeam"] == team and killLog["deathCharacter"] == agent:
						deathOrderBonusXEconFactorSum += killLog["deathOrderBonus*EconFactor"]
						deathOrderBonusXTimeFactorSum += killLog["deathOrderBonus*TimeFactor"]
						deathOrderBonusXEconSwingRiskFactor += killLog["deathOrderBonus*EconSwingRiskFactor"]

			player["RoundInfo"][roundIndex]["deathOrderBonus*EconFactorSum"] = deathOrderBonusXEconFactorSum
			player["RoundInfo"][roundIndex]["deathOrderBonus*TimeFactorSum"] = deathOrderBonusXTimeFactorSum
			player["RoundInfo"][roundIndex]["deathOrderBonus*EconSwingRiskFactorSum"] = deathOrderBonusXEconSwingRiskFactor

	return playersRoundInfo


def checkForResurrection(killLogIndex, roundKillLog):

	agent = roundKillLog[killLogIndex]["deathCharacter"]
	team = roundKillLog[killLogIndex]["deathTeam"]
	for kli in range(killLogIndex + 1, len(roundKillLog)):
		killLog = roundKillLog[kli]
		if killLog["Event"] == "Kill":
			if killLog["deathCharacter"] == agent and killLog["deathTeam"] == team:
				return True
			if killLog["killerCharacter"] == agent and killLog["killerTeam"] == team:
				return True

	return False

def checkForSelfKill(killLog):
	return killLog["deathCharacter"] == killLog["killerCharacter"] and killLog["deathTeam"] == killLog["killerTeam"]

def calculateKillOrderBonuses(roundKillLogs, playersRoundInfo, roundOutcomes):

	for roundIndex in roundKillLogs.keys():

		team1KillIndex = 5
		team2KillIndex = 5
		killsInRound = 0

		planted = False
		plantedTime = 0
		exploded = False
		defused = False

		team1EconSwingRiskFactor = calculateEconSwingRiskFactor(playersRoundInfo, roundOutcomes, roundIndex, "team-1")
		team2EconSwingRiskFactor = calculateEconSwingRiskFactor(playersRoundInfo, roundOutcomes, roundIndex, "team-2")
		# print("round " + str(roundIndex))
		# print("SwingFactor")
		# print(team1EconSwingRiskFactor)
		# print(team2EconSwingRiskFactor)
		# print("~~~~~~~~~~~~~~~~~~~~~\n\n")

		for killLogIndex in range(0, len(roundKillLogs[roundIndex])):
			killLog = roundKillLogs[roundIndex][killLogIndex]

			if killLog["Event"] == "Planted":
				planted = True
				plantedTime = killLog["eventTime"]

			if killLog["Event"] == "Exploded":
				exploded = True

			if killLog["Event"] == "Defused":
				defused = True

			if killLog["Event"] == "Kill":

				selfKill = checkForSelfKill(killLog)

				econSwingRiskFactor = team1EconSwingRiskFactor if killLog["killerTeam"] == "team-2" else team2EconSwingRiskFactor

				killOrderBonus = calculateKillOrderBonus(team1KillIndex, team2KillIndex, killLog["killerTeam"], killsInRound, selfKill)

				killLog["killOrderBonus"] = killOrderBonus if not selfKill else 0
				killLog["killOrderBonus*EconFactor"] = killOrderBonus * killLog["EconomyDifferentialFactor"] if not selfKill else 0

				killLog["killOrderBonus*TimeFactor"] = killOrderBonus * calculateTimeFactor(planted, plantedTime, exploded, defused, killLog["eventTime"]) if not selfKill else 0
				
				killLog["killOrderBonus*EconSwingRiskFactor"] = killOrderBonus * econSwingRiskFactor if not selfKill else 0


				deathOrderBonus = killOrderBonus * calculateTradedFactor(roundKillLogs[roundIndex], killLog, selfKill)

				killLog["deathOrderBonus"] = deathOrderBonus 
				if selfKill:
					if killLog["EconomyDifferentialFactor"] == 4:
						deathEconFactor = .9
					elif killLog["EconomyDifferentialFactor"] == 6:
						deathEconFactor = .75
					else:
						deathEconFactor = .15

				else:
					deathEconFactor = killLog["EconomyDifferentialFactor"]

				killLog["deathOrderBonus*EconFactor"] = deathOrderBonus * (deathEconFactor)
				killLog["deathOrderBonus*TimeFactor"] = deathOrderBonus * calculateTimeFactor(planted, plantedTime, exploded, defused, killLog["eventTime"], forDeath=True)
				killLog["deathOrderBonus*EconSwingRiskFactor"] = deathOrderBonus * econSwingRiskFactor

				killLog["playersOnTeam"] = (team1KillIndex, team2KillIndex)

				resurrection = checkForResurrection(killLogIndex, roundKillLogs[roundIndex])
				killLog["resurrection"] = resurrection
				if not resurrection:
					if selfKill:
						if killLog["killerTeam"] == "team-1":
							team2KillIndex -= 1
						else:
							team1KillIndex -= 1
					else:
						if killLog["killerTeam"] == "team-1":
							team1KillIndex -= 1
						else:
							team2KillIndex -= 1

				killsInRound += 1

	return roundKillLogs

def calculateKillOrderPlayersOnTeam(roundKillLogs, playersRoundInfo, roundOutcomes):

	for roundIndex in roundKillLogs.keys():

		team1KillIndex = 5
		team2KillIndex = 5
		killsInRound = 0

		print("round")
		print(roundIndex)
		for killLogIndex in range(0, len(roundKillLogs[roundIndex])):
			killLog = roundKillLogs[roundIndex][killLogIndex]

			if killLog["Event"] == "Kill":
				selfKill = checkForSelfKill(killLog)

				killLog["playersOnTeam"] = (team1KillIndex, team2KillIndex)
				print(killLog["playersOnTeam"])

				resurrection = checkForResurrection(killLogIndex, roundKillLogs[roundIndex])
				killLog["resurrection"] = resurrection
				if not resurrection:
					if selfKill:
						if killLog["killerTeam"] == "team-1":
							team2KillIndex -= 1
						else:
							team1KillIndex -= 1
					else:
						if killLog["killerTeam"] == "team-1":
							team1KillIndex -= 1
						else:
							team2KillIndex -= 1

				killsInRound += 1

	return roundKillLogs

def calculateEconBonus(roundOutcomes, roundIndex, team):
	teamWon = calculateTeamWon(roundOutcomes, roundIndex, team)

	if teamWon:
		return 3000

	roundVar = int(roundIndex) - 1
	lossStreak = 0
	while roundVar > 0:
		lossStreakTeamWon = calculateTeamWon(roundOutcomes, str(roundVar), team)
		if not lossStreakTeamWon:
			lossStreak += 1
		else:
			break

		roundVar -= 1
			

	if lossStreak == 0:
		return 1900
	elif lossStreak == 1:
		return 2400
	else:
		return 2900


def didTeamWin(roundOutcomes, roundIndex, team):
	roundOutcome = roundOutcomes[roundIndex]
	teamOutcome = roundOutcome.split("Team ")[1][0]
	return (teamOutcome == "A" and team == "team-1") or (teamOutcome == "B" and team == "team-2")


def calculateMinNextRoundEconBonus(roundOutcomes, roundIndex, team):
	lossStreak = roundsSinceLastWin(roundOutcomes, roundIndex, team)

	if lossStreak == 0:
		return 1900
	elif lossStreak == 1:
		return 2400
	else:
		return 2900


def roundsSinceLastWin(roundOutcomes, roundIndex, team):
	roundVar = int(roundIndex) - 1
	lossStreak = 0
	while roundVar > 0:
		teamWon = didTeamWin(roundOutcomes, str(roundVar), team)
		if teamWon:
			return lossStreak

		lossStreak += 1
		roundVar -= 1

	return lossStreak


def calculateEconSwingRiskFactor(playersRoundInfo, roundOutcomes, roundIndex, team):
	
	if roundIndex == "1" or roundIndex == "13":
		return 1.5

	if roundIndex == "12" or roundIndex == "24":
		return 1

	if int(roundIndex) > 24:
		return 1

	loadoutThreshold = 3400
	vandalCost = 2900
	econBonus = calculateMinNextRoundEconBonus(roundOutcomes, roundIndex, team)
	cantBuyNext = 0
	canBuyNext = 0
	canBuyIfWin = 0
	canBuyDouble = 0
	canBuyIfWinDouble = 0
	needToBuyNext = 0
	boughtIn = 0
	for player in playersRoundInfo.keys():
		if playersRoundInfo[player]["Team"] == team:
			remaining = playersRoundInfo[player]["RoundInfo"][int(roundIndex)-1]["Remaining"]
			needToBuyNext += 1 if playersRoundInfo[player]["RoundInfo"][int(roundIndex)-1]["Deaths"] > 0 else 0
			currentLoadout = playersRoundInfo[player]["RoundInfo"][int(roundIndex)-1]["Loadout"]
			
			if remaining + econBonus < loadoutThreshold:
				cantBuyNext += 1

			if remaining + econBonus >= loadoutThreshold:
				canBuyNext += 1

			if remaining + 3000 >= loadoutThreshold:
				canBuyIfWin += 1

			if remaining + econBonus >= (loadoutThreshold + vandalCost):
				canBuyNext -= 1
				canBuyDouble += 1

			if remaining + 3000 >= loadoutThreshold + vandalCost:
				canBuyIfWin -= 1
				canBuyIfWinDouble += 1

			if currentLoadout >= loadoutThreshold:
				boughtIn += 1


	# print("cant buy: " + str(cantBuyNext))
	# print("can buy: " + str(canBuyNext))
	# print("can buy double: " + str(canBuyDouble))
	# print("can buy if win: " + str(canBuyIfWin))
	# print("can buy double if win: " + str(canBuyIfWinDouble))
	# print("\n")

	swingFactor = round((boughtIn + cantBuyNext - canBuyDouble + 3) * .01, 2) 
	# print("SwingFactor: " + str(swingFactor))

	if canBuyNext + 2*canBuyDouble >= 5: #low risk round
		lowRisk = .7 - round(((canBuyNext + 2*canBuyDouble) * .05), 2)
		return round(lowRisk + boughtIn * swingFactor, 1)

	if roundIndex == "2" or roundIndex == "14":
		swingFactor = .15

		return round(1 + (cantBuyNext  * swingFactor) + ((needToBuyNext - canBuyNext) * .1), 2)

	if canBuyNext + canBuyDouble < 5: #risk round
		return round(1 + (.67*(boughtIn * swingFactor) + (cantBuyNext * swingFactor)) + ((needToBuyNext - (canBuyIfWin + 2*canBuyIfWinDouble)) * swingFactor), 2)

	return 1


def calculateTradedFactor(roundKillLog, checkingKillLog, selfKill):

	if selfKill:
		return 1

	timeToTrade = 10

	killerCharacter = checkingKillLog["killerCharacter"]
	killerTeam = checkingKillLog["killerTeam"]
	deathTime = checkingKillLog["eventTime"]

	for killLog in roundKillLog:

		if killLog["Event"] == "Kill":

			if killLog["deathCharacter"] == killerCharacter and killLog["deathTeam"] == killerTeam:

				tradeTime = killLog["eventTime"] - deathTime
				if tradeTime >= 0:
					if tradeTime <= timeToTrade:
						tradeInTimeFactor = tradeTime / 10 
						return tradeInTimeFactor

	return 1


def calculateTimeFactor(planted, plantedTime, exploded, defused, killTime, forDeath=False):
	timeFactor = 1

	if exploded or defused:
		timeFactor = .5
		return timeFactor

	if planted and killTime >= plantedTime + 38 and killTime <= plantedTime + 45:
		# A kill in this window is denying/clutching a near-explosion round, so it's
		# highly valuable. A death in this window isn't the mirror-image punishment -
		# the round is basically already decided in the killer's favor by then - so it
		# gets the same discount as a death after the round has already been decided
		# (exploded/defused) instead of the kill-side bonus.
		timeFactor = .5 if forDeath else 1.75
		return timeFactor

	if planted:
		timeAdditionFactor = (killTime - plantedTime) / 53

		timeFactor = 1 + timeAdditionFactor
		return timeFactor

	return timeFactor


def calculateKillOrderBonus(team1KillIndex, team2KillIndex, killTeam, killsInRound, selfKill):
	G = nx.DiGraph()
	G.add_weighted_edges_from([
		("5v5", "4v5", 150),
		("5v5", "5v4", 150),

		("4v5", "3v5", 130),
		("4v5", "4v4", 140),
		("5v4", "4v4", 140),
		("5v4", "5v3", 130),

		("3v5", "2v5", 90),
		("3v5", "3v4", 120),
		("4v4", "3v4", 170),
		("4v4", "4v3", 170),
		("5v3", "4v3", 120),
		("5v3", "5v2", 90),

		("2v5", "1v5", 50),
		("2v5", "2v4", 70),
		("3v4", "2v4", 130),
		("3v4", "3v3", 160),
		("4v3", "3v3", 160),
		("4v3", "4v2", 130), 
		("5v2", "4v2", 70),
		("5v2", "5v1", 50),

		("1v5", "0v5", 40),
		("1v5", "1v4", 60),
		("2v4", "1v4", 80),
		("2v4", "2v3", 130),
		("3v3", "2v3", 180),
		("3v3", "3v2", 180),
		("4v2", "3v2", 130),
		("4v2", "4v1", 80),
		("5v1", "4v1", 60),
		("5v1", "5v0", 40),

		("1v4", "0v4", 50),
		("1v4", "1v3", 70),
		("2v3", "1v3", 140),
		("2v3", "2v2", 170),
		("3v2", "2v2", 170),
		("3v2", "3v1", 140),
		("4v1", "3v1", 70),
		("4v1", "4v0", 50),

		("1v3", "0v3", 70),
		("1v3", "1v2", 120),
		("2v2", "1v2", 200),
		("2v2", "2v1", 200),
		("3v1", "2v1", 120),
		("3v1", "3v0", 70),

		("1v2", "0v2", 130),
		("1v2", "1v1", 190),
		("2v1", "1v1", 190),
		("2v1", "2v0", 130),

		("1v1", "0v1", 250),
		("1v1", "1v0", 250)
	])



	beforeNode = f"{team1KillIndex}v{team2KillIndex}"
	if selfKill:
		if killTeam == "team-1":
			team2KillIndex -= 1
		else:
			team1KillIndex -= 1
	else:
		if killTeam == "team-1":
			team1KillIndex -= 1
		else:
			team2KillIndex -= 1

	afterNode = f"{team1KillIndex}v{team2KillIndex}"

	try:
		return G[beforeNode][afterNode]["weight"]
	except Exception as e:
		return 100


def reverseAgentTeamToPlayerUsername(playersRoundInfo):

	agentTeamToPlayer = {}
	for username in playersRoundInfo.keys():
		player = playersRoundInfo[username]
		agent = player["Agent"]
		team = player["Team"]

		key = team+agent
		agentTeamToPlayer[key] = username

	return agentTeamToPlayer

def categorizeEcon(EconOfLoadout):
	if EconOfLoadout < 1000:
		return 8 #SAVE
	elif EconOfLoadout < 3300:
		return 6 #ECON
	else:
		return 4 #FULL BUY	

def calculateEconDifferential(playersRoundInfo, roundKillLogs):

	agentTeamToPlayer = reverseAgentTeamToPlayerUsername(playersRoundInfo)

	for roundIndex in roundKillLogs.keys():

		for killLog in roundKillLogs[roundIndex]:
			if killLog["Event"] == "Kill":
				killerKey = killLog["killerTeam"]+killLog["killerCharacter"]
				killerUsername = agentTeamToPlayer[killerKey]
				killerEcon = playersRoundInfo[killerUsername]["RoundInfo"][int(roundIndex)-1]["Loadout"]

				deathKey = killLog["deathTeam"]+killLog["deathCharacter"]
				deathUsername = agentTeamToPlayer[deathKey]
				deathEcon = playersRoundInfo[deathUsername]["RoundInfo"][int(roundIndex)-1]["Loadout"]

				selfKill = checkForSelfKill(killLog)

				killLog["EconomyDifferentialFactor"] = categorizeEcon(killerEcon)/categorizeEcon(deathEcon) if not selfKill else categorizeEcon(deathEcon)

	return roundKillLogs


def updateUsernameToFilenamesJson(playersRoundInfo, filename):
	with open('UsernameToFilename.json', 'r') as file:
		data = json.load(file)

	for username in playersRoundInfo.keys():
		if username in data.keys():
			data[username].append(filename)
		else:
			data[username] = [filename]

		data[username] = list(set(data[username]))


	with open('UsernameToFilename.json', 'w') as file:
		json.dump(data, file, indent=4)

def measureOutgoingImpact(filename):

	htmls = loadHTMLSFromJson(filename)

	playersRoundInfo = parsePlayerRoundInfo(filename)

	updateUsernameToFilenamesJson(playersRoundInfo, filename)
	# print(playersRoundInfo)

	roundOutcomes = parseRoundOutcome(filename)

	roundKillLogs = parseRoundKillList(filename)
	roundKillLogs = calculateEconDifferential(playersRoundInfo, roundKillLogs)
	roundKillLogs = calculateKillOrderBonuses(roundKillLogs, playersRoundInfo, roundOutcomes)
	

	playersRoundInfo = calculateDamageAndAssists_KillOrderSum_KillFactorAverage(playersRoundInfo, roundKillLogs)

	playersRoundInfo = calculateRoundImpact(playersRoundInfo)



	return (roundOutcomes, roundKillLogs, playersRoundInfo)

def getBasicInfo(filename):
	htmls = loadHTMLSFromJson(filename)
	playersRoundInfo = parsePlayerRoundInfo(filename)
	roundOutcomes = parseRoundOutcome(filename)
	roundKillLogs = parseRoundKillList(filename)
	roundKillLogs = calculateKillOrderPlayersOnTeam(roundKillLogs, playersRoundInfo, roundOutcomes)
	return (roundOutcomes, roundKillLogs, playersRoundInfo)


if __name__ == "__main__":
	print("Starting the scraping")
	print("Open the match in a new window on the main screen")

	filename = input("Please enter the map followed by date month year and time Ex: <MAP>MMDDYYHHMM\n")

# save game to files::
	# createMapFolder(filename)

	# input("save the tracker page as a complete webpage into the newly created folder following the same name")

	# saveAllRounds(filename)
	# cleanUpFiles(filename)
	# saveHTMLToJson(filename)
	
# end

	# print(parseEconPerRound(filename))
	# print(parseRoundOutcome(filename))
	# print(parseRoundKillList(filename)["2"])
	# print(parseRoundKillList(filename)["23"])

	# parseTeamPlayers(filename)
	# print(parsePlayerRoundInfo(filename))

	(roundOutcomes, roundKillLogs, playersRoundInfo) = measureOutgoingImpact(filename)
	displayImpact(playersRoundInfo, False)