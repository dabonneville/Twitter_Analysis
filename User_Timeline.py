import argparse, collections, json, math, mysql.connector as sql, os, requests, sys, time
from datetime import datetime
from mysql.connector import errorcode
from requests import HTTPError
from requests import ConnectionError
from requests_oauthlib import OAuth1
import pprint as pp
import re

# Print strings in verbose mode
def verbose(info) :
	if args.verbose:
		printUTF8(info)

def printUTF8(info) :
	print(info.encode('ascii', 'replace').decode())

# Connect to MySQL using config entries - temporarily replace configparser with strings until Python3 upgrade
def connect() :

	db_params = {
		'user' : "root",
		'password' : "",
		'host' : "localhost",
		'port' : 3306,
		'database' : "Twitter",
		'charset' : 'utf8',
		'collation' : 'utf8_general_ci',
		'buffered' : True
	}

	return sql.connect(**db_params)

# Get all jobs from the database
def getJobs(conn) :
	cursor = conn.cursor() 

	query = ("SELECT job_id, head, query, since_id_str, description, \
				oauth.oauth_id, consumer_key, consumer_secret, access_token, access_token_secret \
			FROM job, oauth \
			WHERE  job.oauth_id = oauth.oauth_id AND head = %s \
			ORDER BY job_id")

	cursor.execute(query,[args.head])
	return cursor


def getFullQuery(query, since_id_str) :
	return "?screen_name=" + str(query) + "&since_id=" + str(since_id_str)  + "&count=1000"


# Query Twitter's Search 1.1 API
def getUserTimeline(query, oauth) :
	verbose("Query: " + query)

	attempt = 1
	while attempt <= 3 :
		try :
			#print("https://api.twitter.com/1.1/statuses/user_timeline.json" + query)
			r = requests.get("https://api.twitter.com/1.1/statuses/user_timeline.json" + query, auth=oauth)
			return json.loads(r.text)			#Return a list/dict formatted result

		except (ConnectionError, HTTPError) as err :
			sleep_time = 2**(attempt - 1)
			verbose("Connection attempt " + str(attempt) + " failed. "
				"Sleeping for " + str(sleep_time) + " second(s).")
			time.sleep(sleep_time)
			attempt = attempt + 1

	print("***** Error: Unable to query Twitter. Terminating.")
	

# Add a tweet to the DB
def addTweet(conn, job_id, tweet) :
	cursor = conn.cursor()

	wordcount = word_count(tweet["text"])

	prefix = "INSERT INTO user_timeline (tweet_id_str, job_id, created_at, text, word_count, tweet_retweet_count, from_user, from_user_id_str, " \
		"from_user_name, from_user_fullname, from_user_followers, from_user_following, from_user_favorites, " \
		" from_user_tweets, from_user_timezone, to_user, to_user_id_str, to_user_name, source, iso_language"
	suffix = ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"
	values = [
		tweet["id_str"],
		job_id,
		datetime.strptime(tweet["created_at"], '%a %b %d %H:%M:%S +0000 %Y').strftime('%Y-%m-%d %H:%M:%S'),
		tweet["text"],
		wordcount,
		#tweet["favourites_count"],
		tweet["retweet_count"],
		tweet["user"]["id"],
		tweet["user"]["id_str"],
		tweet["user"]["screen_name"],
		tweet["user"]["name"],
		tweet["user"]["followers_count"],
		tweet["user"]["friends_count"],
		tweet["user"]["favourites_count"],
		tweet["user"]["statuses_count"],
		tweet["user"]["time_zone"],
		tweet["in_reply_to_user_id"],
		tweet["in_reply_to_user_id_str"],
		tweet["in_reply_to_screen_name"],
		re.sub('<[^>]*>', '', tweet['source']),			# removing the <a href>...</a> tags
		tweet["user"]["lang"]
	]
	
	suffix = suffix + ")"
	query = (prefix + suffix)
	
	try :
		cursor.execute(query, values)
		conn.commit()
		return True
	except sql.Error as err :
		verbose("")
		verbose(">>>> Warning: Could not add Tweet: " + str(err))
		verbose("     Query: " + cursor.statement)
		return False
	finally :
		cursor.close()

# Add hashtag entities to the DB
def addHashtags(conn, job_id, tweet) :
	cursor = conn.cursor()

	query = "INSERT IGNORE INTO hashtag (tweet_id, job_id, text, index_start, index_end) " \
		"VALUES(%s, %s, %s, %s, %s)"

	for hashtag in tweet['entities']['hashtags'] :
		values = [
			tweet["id_str"], 
			job_id, 
			hashtag["text"], 
			hashtag["indices"][0],
			hashtag["indices"][1] 
		]

		try :
			cursor.execute(query, values)
			conn.commit()
		except sql.Error as err :
			verbose("")
			verbose(">>>> Warning: Could not add Hashtag: " + str(err))
			verbose("     Query: " + cursor.statement)
	
	cursor.close()

# Add user mention entities to the DB
def addUserMentions(conn, job_id, tweet) :
	cursor = conn.cursor()

	query = "INSERT IGNORE INTO mention (tweet_id, job_id, screen_name, name, id_str, index_start, index_end) " \
		"VALUES(%s, %s, %s, %s, %s, %s, %s)"

	for mention in tweet['entities']['user_mentions'] :
		values = [
			tweet["id_str"], 
			job_id, 
			mention["screen_name"], 
			mention["name"], 
			mention["id_str"], 
			mention["indices"][0],
			mention["indices"][1] 
		]

		try :
			cursor.execute(query, values)
			conn.commit()
		except sql.Error as err :
			verbpse("")
			verbose(">>>> Warning: Could not add User Mention: " + str(err))
			verbose("     Query: " + cursor.statement)
	
	cursor.close

# Add all URL entities to the DB
def addURLS(conn, job_id, tweet) :
	cursor = conn.cursor()

	query = "INSERT INTO url (tweet_id, job_id, url, expanded_url, display_url, index_start, index_end) " \
		"VALUES(%s, %s, %s, %s, %s, %s, %s)"

	for url in tweet['entities']['urls'] :
		values = [
			tweet["id_str"], 
			job_id, 
			url["url"], 
			url["expanded_url"] if "expanded_url" in url else "", 
			url["display_url"] if "display_url" in url else "", 
			url["indices"][0],
			url["indices"][1] 
		]

		try :
			cursor.execute(query, values)
			conn.commit()
		except sql.Error as err :
			verbose("")
			verbose(">>>> Warning: Could not add URL: " + str(err))
			verbose("     Query: " + cursor.statement)
	
	cursor.close()

# Update the stored job's since_id to prevent retrieving previously processed tweets
def updateSinceId(conn, job_id, max_id_str, total_results) :
	cursor = conn.cursor()

	query = "UPDATE job SET since_id_str=%s, last_count=%s, last_run=%s WHERE job_id=%s"

	values = [
		max_id_str,
		total_results,
		datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
		job_id
	]

	try :
		cursor.execute(query, values)
		conn.commit()
	except sql.Error as err :
		verbose(">>>> Warning: Could not update job: " + str(err))
		verbose("     Query: " + cursor.statement)
	finally:
		cursor.close()

# Add an entry into the job history table
def addHistory(conn, job_id, oauth_id, success, total_results = 0) :
	cursor = conn.cursor()

	query = "INSERT INTO history (job_id, oauth_id, timestamp, status, total_results) " \
		"VALUES(%s, %s, %s, %s, %s)"

	values = [
		job_id,
		oauth_id,
		datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
		"success" if success else "failure",
		total_results
	]

	try :
		cursor.execute(query, values)
		conn.commit()
	except sql.Error as err :
		verbose(">>>> Warning: Could not add history entry: " + str(err))
		verbose("     Query: " + cursor.statement)
	finally:
		cursor.close()

def word_count(tweet):
	wordcount = 0
	words = tweet.split()
	return(wordcount + len(words))


# Main function
if __name__ == '__main__' :
	# Handle command line arguments
	parser = argparse.ArgumentParser(description="Same as before")
	parser.add_argument('head', type=int, help="Specify the head #")
	parser.add_argument('-v','--verbose', default=False, action="store_true", help="Show additional logs")
	parser.add_argument('-d','--delay', type=int, default=0, help="Delay execution by DELAY seconds")
	args = parser.parse_args()


	# Display startup info
	print("vvvvv Start:", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
	verbose("Verbose Mode: Enabled")
	print("Head:", args.head)
	print("Delay:", args.delay)
	
	epoch_min = math.floor(time.time() / 60)
	verbose("Epoch Minutes: " + str(epoch_min))

	if (args.delay > 0) :
		time.sleep(args.delay)

	print("Connecting to database...")

	try :
		run_total_count = 0
		conn = connect()
		print("Connected")

		# Get all of the jobs for this head
		jobs = getJobs(conn)
		
		if not jobs.rowcount :
			print("\nUnable to find any jobs to run. Please make sure there are entries in the 'job'"
				+ " table that have an oauth_id corresponding to an entry in the 'oauth', the 'head'"
				+ " value matches {}, and the 'state' value is greater than 0.\n".format(args.head))

		# Iterate over all of the jobs found
		for (job_id, head, query, since_id_str, description, oauth_id, 
				consumer_key, consumer_secret, access_token, access_token_secret) in jobs :
			

			printUTF8("+++++ Job ID:" + str(job_id) + "\tDescription:" + description + "\tQuery:" + query + "\tOAuth ID:" + str(oauth_id))
			print("Continue")
			oauth = OAuth1(client_key=consumer_key,
						client_secret=consumer_secret,
						resource_owner_key=access_token,
						resource_owner_secret=access_token_secret)

			# Get the Tweets
			results = getUserTimeline(getFullQuery(query, since_id_str), oauth)

			# Make sure that we didn't receive an error instead of an actual result
			if "errors" in results :
				for error in results["errors"] :
					verbose("      Error response received: " + error["message"])

				print("***** Error: Unable to query Twitter. Ending job.")
				addHistory(conn, job_id, oauth_id, False)
				continue

			tweets = collections.deque()
			
			tweets.extend(results)

			# Search results are returned in a most-recent first order, so we only need the inital max
			max_id_str =  results[0]["id_str"]
			verbose("Max ID: " + str(max_id_str))
			total_results = 0
			count = 1
			total = len(tweets)

			while tweets :
				total_results = total_results + 1
				tweet = tweets.popleft()
				
				# Insert the tweet in the DB
				success = addTweet(conn, job_id, tweet)
			
				# Show status logging
				if args.verbose :
					sys.stdout.write("\rProgress: " + str(count) + "/" + str(total))
				count = count + 1
				
				# Insert the tweet entities in the DB
				if success :
					addHashtags(conn, job_id, tweet)
					addUserMentions(conn, job_id, tweet)
					addURLS(conn, job_id, tweet)
		

			verbose("")
			print("Total Results:", total_results)
			run_total_count = run_total_count + total_results

			# Update the since_id to use for future tweets		
			updateSinceId(conn, job_id, max_id_str, total_results)
			addHistory(conn, job_id, oauth_id, True, total_results)
					
	except sql.Error as err :
		print(err)
		print("Terminating.")
		sys.exit(1)
	else :
		conn.close()
	finally :
		print("$$$$$ Run total count: " + str(run_total_count))
		print("^^^^^ Stop:", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))