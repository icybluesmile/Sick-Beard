# Author: Nic Wolfe <nic@wolfeden.ca>
# URL: http://code.google.com/p/sickbeard/
#
# This file is part of Sick Beard.
#
# Sick Beard is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Sick Beard is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with Sick Beard.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import with_statement

import traceback

from lib.tvnamer.utils import FileParser
from lib.tvnamer import tvnamer_exceptions

import sickbeard

from common import *

from sickbeard import logger
from sickbeard import sab
from sickbeard import history

from sickbeard import notifiers 
from sickbeard import exceptions

from sickbeard.providers import *
from sickbeard import providers

def _downloadResult(result):

	resProvider = providers.getProviderModule(result.provider)

	newResult = False

	if resProvider == None:
		logger.log("Invalid provider name - this is a coding error, report it please", logger.ERROR)
		return False

	if resProvider.providerType == "nzb":
		newResult = resProvider.downloadNZB(result)
	elif resProvider.providerType == "torrent":
		newResult = resProvider.downloadTorrent(result)
	else:
		logger.log("Invalid provider type - this is a coding error, report it please", logger.ERROR)
		return False

	return newResult

def snatchEpisode(result, endStatus=SNATCHED):

	if result.resultType == "nzb":
		if sickbeard.NZB_METHOD == "blackhole":
			dlResult = _downloadResult(result)
		elif sickbeard.NZB_METHOD == "sabnzbd":
			dlResult = sab.sendNZB(result)
		else:
			logger.log("Unknown NZB action specified in config: " + sickbeard.NZB_METHOD, logger.ERROR)
			dlResult = False
	elif result.resultType == "torrent":
		dlResult = _downloadResult(result)
	else:
		logger.log("Unknown result type, unable to download it", logger.ERROR)
		dlResult = False
	
	if dlResult == False:
		return

	history.logSnatch(result)

	# don't notify when we re-download an episode
	for curEpObj in result.episodes:
		if curEpObj.status in Quality.DOWNLOADED:
			notifiers.notify(NOTIFY_SNATCH, curEpObj.prettyName(True))
	
		with curEpObj.lock:
			curEpObj.status = Quality.compositeStatus(endStatus, result.quality)
			curEpObj.saveToDB()

	sickbeard.updateAiringList()
	sickbeard.updateComingList()

def searchForNeededEpisodes():
	
	logger.log("Searching all providers for any needed episodes")

	foundResults = {}

	didSearch = False

	# ask all providers for any episodes it finds
	for curProvider in providers.getAllModules():
		
		if not curProvider.isActive():
			continue
		
		curFoundResults = {}
		
		try:
			curFoundResults = curProvider.searchRSS()
		except exceptions.AuthException, e:
			logger.log("Authentication error: "+str(e), logger.ERROR)
			continue
		except Exception, e:
			logger.log("Error while searching "+curProvider.providerName+", skipping: "+str(e), logger.ERROR)
			logger.log(traceback.format_exc(), logger.DEBUG)
			continue

		didSearch = True
		
		# pick a single result for each episode, respecting existing results
		for curEp in curFoundResults:
			
			if curEp.show.paused:
				logger.log("Show "+curEp.show.name+" is paused, ignoring all RSS items for "+curEp.prettyName(True), logger.DEBUG)
				continue
			
			# find the best result for the current episode
			bestResult = None
			for curResult in curFoundResults[curEp]:
				if not bestResult or bestResult.quality < curResult.quality:
					bestResult = curResult
			
			bestResult = pickBestResult(curFoundResults[curEp])
			
			# if it's already in the list (from another provider) and the newly found quality is no better then skip it
			if curEp in foundResults and bestResult.quality <= foundResults[curEp].quality:
				continue

			foundResults[curEp] = bestResult

	if not didSearch:
		logger.log("No providers were used for the search - check your settings and ensure that either NZB/Torrents is selected and at least one NZB provider is being used.", logger.ERROR)

	return foundResults.values()


def pickBestResult(results):

	# find the best result for the current episode
	bestResult = None
	for curResult in results:
		if not bestResult or bestResult.quality < curResult.quality:
			bestResult = curResult
	
	return bestResult


def findEpisode(episode, manualSearch=False):

	logger.log("Searching for " + episode.prettyName(True))

	foundResults = []

	didSearch = False

	for curProvider in providers.getAllModules():
		
		if not curProvider.isActive():
			continue
		
		try:
			foundResults = curProvider.findEpisode(episode, manualSearch=manualSearch)
		except exceptions.AuthException, e:
			logger.log("Authentication error: "+str(e), logger.ERROR)
			continue
		except Exception, e:
			logger.log("Error while searching "+curProvider.providerName+", skipping: "+str(e), logger.ERROR)
			logger.log(traceback.format_exc(), logger.DEBUG)
			continue
		
		didSearch = True
		
		# skip non-tv crap
		foundResults = filter(lambda x: all([y not in x.extraInfo[0].lower() for y in resultFilters]), foundResults)
		
		if len(foundResults) > 0:
			break
	
	if not didSearch:
		logger.log("No providers were used for the search - check your settings and ensure that either NZB/Torrents is selected and at least one NZB provider is being used.", logger.ERROR)

	bestResult = pickBestResult(foundResults)
	
	return bestResult

def findSeason(show, season):
	
	logger.log("Searching for stuff we need from "+show.name+" season "+str(season))
	
	foundResults = {}
	
	didSearch = False
	
	for curProvider in providers.getAllModules():
		
		if not curProvider.isActive():
			continue
		
		try:
			curResults = curProvider.findSeasonResults(show, season)

			# make a list of all the results for this provider
			for curEp in curResults:
				# skip non-tv crap
				curResults[curEp] = filter(lambda x: all([y not in x.extraInfo[0].lower() for y in resultFilters]), curResults[curEp])
				
				if curEp in foundResults:
					foundResults[curEp] += curResults[curEp]
				else:
					foundResults[curEp] = curResults[curEp]
		
		except exceptions.AuthException, e:
			logger.log("Authentication error: "+str(e), logger.ERROR)
			continue
		except Exception, e:
			logger.log("Error while searching "+curProvider.providerName+", skipping: "+str(e), logger.ERROR)
			logger.log(traceback.format_exc(), logger.DEBUG)
			continue
		
		didSearch = True
		
		if len(foundResults) > 0:
			break
	
	if not didSearch:
		logger.log("No providers were used for the search - check your settings and ensure that either NZB/Torrents is selected and at least one NZB provider is being used.", logger.ERROR)
	
	finalResults = []

	# go through multi-ep results and see if we really want them or not, get rid of the rest
	if -1 in foundResults:
		for multiResult in foundResults[-1]:
			
			logger.log("Seeing if we want to bother with multi-episode result "+multiResult.extraInfo[0], logger.DEBUG)
			
			# see how many of the eps that this result covers aren't covered by single results
			neededEps = []
			notNeededEps = []
			for epObj in multiResult.episodes:
				epNum = epObj.episode
				# if we have results for the episode
				if epNum in foundResults and len(foundResults[epNum]) > 0:
					# but the multi-ep is worse quality, we don't want it
					if False and multiResult.quality <= pickBestResult(foundResults[epNum]):
						notNeededEps.append(epNum)
					else:
						neededEps.append(epNum)
				else:
					neededEps.append(epNum)
	
			logger.log("Result is neededEps: "+str(neededEps)+", notNeededEps: "+str(notNeededEps), logger.DEBUG)
	
			if not neededEps:
				logger.log("All of these episodes were covered by single nzbs, ignoring this multi-ep result", logger.DEBUG)
				continue
			
			# don't bother with the single result if we're going to get it with a multi result
			for epObj in multiResult.episodes:
				epNum = epObj.episode
				if epNum in foundResults:
					logger.log("A needed multi-episode result overlaps with episode "+str(epNum)+", removing its results from the list", logger.DEBUG)
					del foundResults[epNum]
			
			finalResults.append(multiResult)
	
	# of all the single ep results narrow it down to the best one for each episode
	for curEp in foundResults:
		if curEp == -1:
			continue
		
		if len(foundResults[curEp]) == 0:
			continue
		
		finalResults.append(pickBestResult(foundResults[curEp]))
	
	return finalResults
