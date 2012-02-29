# -*- coding: utf-8 -*-
###########################################################################
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth import authenticate, login
from django.contrib.auth import logout

# internationalization
from django.utils.translation import ugettext as _

# Permissions
from django.contrib.auth.models import Group,Permission
from django.contrib.contenttypes.models import ContentType
import md5

from django.http import HttpResponse
from django.shortcuts import render_to_response, redirect
from django.template import RequestContext, loader, Context, Template

from django.conf import settings

# to send emails for registration
from django.core.mail import send_mail

import glob
import os

# just to make unique folder name when uploading enquete
# note that time already imported in models.py
from time import time

# to remove folder recursively (shutil.rmtree("/foldername")
import shutil

from django.core.servers.basehttp import FileWrapper

# reverse() in code ====== {%url .. %} in Template
from django.core.urlresolvers import reverse

# used to manual send token in context for eBrowse (then eAdd)
from django.middleware.csrf import get_token
from django.core import serializers

# for esbrowse, visualization.json, etc..
from django.utils import simplejson

from xml.etree.ElementTree import ElementTree
from lxml import etree

from reanalyse.reanalyseapp.utils import *
from reanalyse.reanalyseapp.models import *
from reanalyse.reanalyseapp.imexport import *
from reanalyse.reanalyseapp.forms import *
from reanalyse.reanalyseapp.visualization import *
from reanalyse.reanalyseapp.search import *

# Search with haystack
from haystack.views import *
from haystack.forms import *
from haystack.query import *

# to update_index SOLR from view
from haystack.management.commands import update_index, clear_index

# Pagination for edShow
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

# for aggregation/annotation
from django.db.models import Count,Max,Sum

# Threading when launching longtime processes (parsing, styling, ...)
import threading

# replacing \n by <br/> when returning speaker.content
import re

# pythonsolr for raw_queries involving termVectors (pysolr does not)
import pythonsolr

###########################################################################
# LOGGING
###########################################################################
import logging
logger = logging.getLogger('apps')
class NullHandler(logging.Handler):
    def emit(self, record):
        pass
nullhandler = logger.addHandler(NullHandler())
###########################################################################








###########################################################################
# SOLR simple process manager
###########################################################################
SOLR_JARNAME = "startreanalysesolr.jar"
###############################
def checkSolrProcess():
	tmp = os.popen("ps -Af").read()
	if SOLR_JARNAME not in tmp[:]:
		logger.info("solr is not running. Let's restart it")
		newprocess = "cd %s && nohup java -jar %s > %s &" % (settings.REANALYSEPROJECTPATH+"/solr/",SOLR_JARNAME,settings.REANALYSELOGSOLR)
		os.system(newprocess)
		return True
	else:
		return False
###############################
@login_required
def killSolrProcess(request):
	if request.user.is_staff:
		killcmd = "kill `ps -ef | grep "+SOLR_JARNAME+" | grep -v grep | awk '{print $2}'`"
		os.system(killcmd)
		res = {'status':"solr killed"}
	else:
		res = {'status':"nothing done. you need rights to do it"}
	return HttpResponse(simplejson.dumps(res,indent=4,ensure_ascii=False), mimetype="application/json")
###########################################################################






###########################################################################
# done when looking at the admin view
def init_users():
	######### Groups & Permissions
	browse = Permission.objects.get(codename='can_browse')
	explore = Permission.objects.get(codename='can_explore')
	make = Permission.objects.get(codename='can_make')
	
	bGroup,isnew = Group.objects.get_or_create(name='BROWSE')
	bGroup.permissions = [browse]
	eGroup,isnew = Group.objects.get_or_create(name='EXPLORE')
	eGroup.permissions = [browse, explore]
	cGroup,isnew = Group.objects.get_or_create(name='CREATE')
	cGroup.permissions = [browse, explore, make]
	
	######### Permissions for Enquete ids
	#content_type,isnew = ContentType.objects.get_or_create(app_label='reanalyseapp', model='Enquete')
	#eEnquete1,isnew = Permission.objects.get_or_create(codename='can_explore_1',name='EXPLORE enquete 1',content_type=content_type)
	#eEnquete2,isnew = Permission.objects.get_or_create(codename='can_explore_2',name='EXPLORE enquete 2',content_type=content_type)
		
	######## Users
	bUser,isnew = User.objects.get_or_create(username='browse',password='-',is_active=True)
	bUser.groups.add(bGroup)
	eUser,isnew = User.objects.get_or_create(username='explore',password='-',is_active=True)
	eUser.groups.add(eGroup)
	#eUser.user_permissions.add(eEnquete2)
	cUser,isnew = User.objects.get_or_create(username='create',password='-',is_active=True)
	cUser.groups.add(cGroup)
	#cUser.user_permissions.add(eEnquete1)
	#cUser.user_permissions.add(eEnquete2)
###########################################################################



	




###########################################################################
# adds key depending on current user & enquete
def updateCtxWithPerm(ctx,request,e):
	user = request.user
	permexplorethis = 'reanalyseapp.can_explore_'+str(e.id)
	#permexplore = Permission.objects.get(codename='can_explore')
	#permmake = Permission.objects.get(codename='can_make')
	eGroup = Group.objects.get(name='EXPLORE')
	cGroup = Group.objects.get(name='CREATE')
	canexplorethis = user.has_perm(permexplorethis) or eGroup in user.groups.all() or cGroup in user.groups.all()
	ctx.update({'permexplorethis':canexplorethis})
###########################################################################
def updateCtxWithSearchForm(ctx):
	form = SentenceSearchForm(load_all=False)
	ctx.update({'form':form})
###########################################################################







###########################################################################
# MAIN VIEWS (static pages + login/register views)
###########################################################################
@login_required
def eDelete(request,eid):
	if request.user.is_staff:
		e = Enquete.objects.get(id=eid)
		# remove uploaded/decompressed files
		os.system("rm -R "+e.locationpath)
		# remove graph files if there is
		for vtyp in GRAPHTYPES:
			for v in e.visualization_set.filter(viztype=vtyp):
				os.system("rm -R "+v.locationpath)
		e.delete()
	return render_to_response('bq_e_browse.html', context_instance=RequestContext(request))
###########################################################################
def logoutuser(request):
	logout(request)
	return redirect(settings.LOGIN_REDIRECT_URL)
###########################################################################




###########################################################################
# fetching html pages stored in template, or stored html content in db
def getStaticHtmlContent(name,lang):
	filepath = settings.REANALYSESITECONTENTPATH + name+'_content_'+lang+'.html'
	sc,isnew = SiteContent.objects.get_or_create(name=name,lang=lang.upper(),description=name)
	# if no object exists in db, get file from templates folder
	if isnew:
		contenthtml = getContentOfFile(filepath)
		sc.contenthtml = contenthtml
		sc.save()
	else:
		contenthtml = sc.contenthtml
	return contenthtml
###########################################################################
def home(request):
	# (p)pname 	is the menu section		'project','method','access',...
	# (q)spname	is the subpage			'0','1' for subsections		OR	'login','register' for login
	
	pname = request.GET.get('p','project')
	spname = request.GET.get('q','0')
	ctx={}
	
	curLang = request.LANGUAGE_CODE
	curUser = request.user

	################################################################################### STATIC PAGE (PROJECT/METHOD)
	if pname in ['project','method']:
		# temp (remove once all pages activated)
		if pname=='method' and spname=='0':
			spname='1'
		
		contenthtml = getStaticHtmlContent(pname,curLang)
		
		# split html file based on <h1> tags and return parts
		pageparts = re.split('<h1>',contenthtml)[1:]
		contenthtml = '<h1>'+pageparts[int(spname)]
		subpages = []
		for i,sp in enumerate(pageparts):
			txttitl = re.split('</h1>',sp)[0]
			txtcont = re.split('</h1>',sp)[1]
			isActive = len(txtcont)>10
			subpages.append([str(i),txttitl,isActive])
		t = Template(contenthtml)
		c = RequestContext(request)
		contenthtml = t.render(c)
		ctx.update({'contenthtml':contenthtml,'subpages':subpages})
	
	#################################################################################### LOGIN/REGISTER PAGE (ACCESS)
	if pname=='access':
 		if spname=='0':
 			spname='login'
 		if curUser.is_authenticated():
 			# get htmlcontent
			ctx.update({'contenthtml':getStaticHtmlContent('access',curLang)})
			spname='register'
 		if spname=='login':
 			# get htmlcontent
			ctx.update({'contenthtml':getStaticHtmlContent('access',curLang)})
			################################################ Login form
			loginform = AuthenticationForm(None, request.POST or None)
			ctx.update({'form_login':loginform})
			if loginform.is_valid():
				login(request, loginform.get_user())
				nextpage = request.GET.get('next', settings.LOGIN_REDIRECT_URL)
				return redirect(nextpage)
 		if spname=='register':
			################################################ In case of user creation
			if not curUser.has_perm('reanalyseapp.can_browse'):
				if request.method == 'POST':
					form = BrowseUserForm(request.POST)
				else:
					form = BrowseUserForm()
				if form.is_valid():		
					new_user = form.save()
					new_user.is_active = False
					new_user.save()
					group = Group.objects.get(name='BROWSE')
					new_user.groups.add(group)
					new_user.save()
					subject = '[reanalyse] reqDISCOVER '+form.cleaned_data['email']
					message = 'username: '+form.cleaned_data['username']
					message += '\nprenom: '+form.cleaned_data['first_name']
					message += '\nnom: '+form.cleaned_data['last_name']
					message += '\nemail: '+form.cleaned_data['email']
					message += '\naffiliation: '+form.cleaned_data['affiliation']
					footer = '\n\nPlease go to '+settings.REANALYSEURL+'/reanalyse/admin/auth/user/'+str(new_user.id)+' to activate this user.'
					message += '\n\n==============================\n'+footer
					send_mail(subject,message,new_user.email,[settings.STAFF_EMAIL],fail_silently=False)
				ctx.update({'form_request':form})
			################################################
			else: # can_browse, asking for can_explore	
				eid = int(request.GET.get('e',-1))
				if eid!=-1:
					wantedenquete = Enquete.objects.get(id=eid)
					if request.method == 'POST':
						form = ExploreUserForm(request.POST)
					else:
						form = ExploreUserForm(initial={'enqueteid':eid})
					if form.is_valid():
						# don't add permission corresponding to the current enquete
						#curPerm = Permission.objects.get(codename='can_explore_'+str(eid))
						#curUser.user_permissions.add(curPerm)
						#curUser.save()
						# rather send mail to CDSP staff
						subject = '[reanalyse] reqEXPLORE: '+wantedenquete.name
						message = 'motivation:\n\n'+form.cleaned_data['motivation']
						footer = 'Please go to '+settings.REANALYSEURL+'/reanalyse/admin/auth/user/'+str(curUser.id)+'  to add permission for this study (permission: EXPLORE e_'+str(wantedenquete.id)+')/'
						message += '\n\n==============================\n'+footer
						send_mail(subject,message,curUser.email,[settings.STAFF_EMAIL],fail_silently=False)
					ctx.update({'form_request':form,'wantedenquete':wantedenquete})
				else:
					# no enqueteid was specified
					donothing=1
					
	# Translators: home view				
	ctx.update({'bodyid':pname,'pageid':spname})
		
	return render_to_response('bq_home.html',ctx,context_instance=RequestContext(request))
###########################################################################





################################################################################
@login_required
def clearLogFile(request):
	try:
		logFile = open(settings.REANALYSELOGDJANGO,'w')
		logFile.write("log cleared\n")
		logFile.close()
		res='log file cleared'
	except:
		res='problem clearing logfile'	
	d={}
	d['status']=res
	return HttpResponse( simplejson.dumps(d,indent=4,ensure_ascii=False), mimetype="application/json")
################################################################################





################################################################################
# UPLOAD & PARSE enquete
################################################################################
@login_required
def eAdmin(request):
	### unique foldername if some upload is done 
	sessionFolderName = "up_"+str(time())
	ctx = {'bodyid':'admin','foldname':sessionFolderName}
	
	### todo: move that somewhere else to do it just when website/database is reset
	try:
		init_users()
	except:
		donothing=1
	
	### check if solr launched, relaunch it if needed
	if checkSolrProcess():
		ctx.update({'solrstatus':'was off. but refreshing this page has relaunched it. wait 5,7s and refresh again to be sure'})
	else:
		ctx.update({'solrstatus':'is running !'})
	ctx.update({'staffemail':settings.STAFF_EMAIL})
	
	### log file
	#logger.info("Looking at ADMIN page")
	wantedCount = int(request.GET.get('log','50'))
	log_django 	= getTailOfFile(settings.REANALYSELOGDJANGO,wantedCount)
	#log_solr 	= getTailOfFile(settings.REANALYSELOGSOLR,wantedCount)
	ctx.update({'log_django':log_django})
		
	### solr path
	ctx.update({'BASE_URL':settings.BASE_URL,'solr_url':settings.SOLR_URL})
	
	### all enquetes
	ctx.update({'enquetes':Enquete.objects.all()})
	
	### default page is 'users'
	ctx.update({'page':request.GET.get('page','users')})
	
	return render_to_response('bq_admin.html', ctx , context_instance=RequestContext(request))
################################################################################
@login_required
def eAddAjax(request):
	d={}
	success=""
	if request.method == "POST":
		foldname = request.GET['foldname']
		if request.is_ajax( ):
			upload = request
			is_raw = True
			try:
				filename = request.GET['qqfile']
			except KeyError:
				return HttpResponseBadRequest( "AJAX request not valid" )
		else:
			is_raw = False
			if len( request.FILES ) == 1:
				upload = request.FILES.values( )[ 0 ]
			else:
				raise Http404( "Bad Upload" )
			filename = upload.name
	
		success = save_upload( upload, foldname, filename, is_raw )
		d['success'] = success
		d['loc'] = foldname+"/"+filename
	jsondata = simplejson.dumps(d,indent=4,ensure_ascii=False)
	return HttpResponse(jsondata, mimetype="application/json")
################################################################################
@login_required
def save_upload( uploaded, foldname, filename, raw_data ):
	''' 
	raw_data: if True, uploaded is an HttpRequest object with the file being
			the raw post data 
			if False, uploaded has been submitted via the basic form
			submission and is a regular Django UploadedFile in request.FILES
	'''
	try:
		from io import FileIO, BufferedWriter
		# check if dir exist, create it if needed
		wantedDir = settings.REANALYSEUPLOADPATH+"/"+foldname
		if not os.path.exists(wantedDir):
			os.mkdir(wantedDir)
		with BufferedWriter( FileIO( wantedDir+"/"+filename, "wb" ) ) as dest:
			# if the "advanced" upload, read directly from the HTTP request 
			# with the Django 1.3 functionality
			if raw_data:
				foo = uploaded.read( 1024 )
				while foo:
					dest.write( foo )
					foo = uploaded.read( 1024 ) 
			# if not raw, it was a form upload so read in the normal Django chunks fashion
			else:
				for c in uploaded.chunks( ):
					dest.write( c )
		# got through saving the upload, report success
		return True
	except IOError:
		# could not open the file most likely
		pass
	return False
################################################################################
@login_required
def eParse(request):
	d={}
	folname = request.GET.get('foldname','')	
	upPath = settings.REANALYSEUPLOADPATH+folname+"/"
	logger.info("PARSING UPLOADED STUDY: "+upPath )
	
	######## (DEPRECATED) uploaded everything at once 
	if os.path.exists(upPath+"ddi.xml"):
		e = importEnqueteDDI2(upPath+"ddi.xml")
	######## archive folder
	elif os.path.exists(upPath+"archive.zip"):
		os.mkdir(upPath+"extracted")
		unzipper = unzip()
		unzipper.extract(upPath+"archive.zip",upPath+"extracted/")
		if os.path.exists(upPath+"extracted/ddi.xml"):
			########### (DEPRECATED) there is a ddi.xml file pointing at every other file of the study
			e = importEnqueteDDI2(upPath+"extracted/ddi.xml")
		elif os.path.exists(upPath+"extracted/_meta/"):
			########### (GOOD WAY!) there is the _meta folder, containing meta_documents.csv, ...
			e = importEnqueteUsingMeta(upPath+"extracted/")
		else:
			logger.info("no zipped ddi.xml file nor _meta folder found")
	else:
		logger.info("no ddi.xml nor archive.zip found")
	
	logger.info("["+str(e.id)+"] enquete imported")
	
	e.status='1'
	e.statuscomplete=0
	e.save()
	
	makeViz(e,'Overview')		# for left-menu-facets
	makeViz(e,'Attributes')		# with all speakers (since they should be defined in meta_speakers.csv table)
	
	####### PARSE TEI and UPDATE INDEX (and save statuscomplete to know loading status)
	# we look at '5'='Waiting' TEI Documents...
	docsTotal = e.texte_set.filter(doctype='TEI',status='5').count()
	docsCur = 0
	for t in e.texte_set.filter(doctype='TEI',status='5').order_by('name'):
		logger.info("["+str(e.id)+"] now parsing text: "+str(t.id) )
		t.parseXml()
		logger.info("["+str(e.id)+"] texte parsed, now updating solr index: "+str(t.id) )
		update_index.Command().handle(verbosity=0)
		logger.info("["+str(e.id)+"] solr index updated")
		docsCur+=1
		e.statuscomplete = int(docsCur*100/docsTotal)
		e.save()
		
		# let's make stream timeline viz for each text
		#logger.info("make streamtimeline viz")
		try:
			makeViz(e,"TexteStreamTimeline",textes=[t])
		except:
			logger.info("["+str(e.id)+"] EXCEPT making streamtimeline viz for texte id: "+str(t.id))
		
	logger.info("["+str(e.id)+"] all texts were sucessfully parsed")
	
	####### UPDATE ALL TFIDF
	# ie fetch ngrams from solr and store them in django model (easier then to make viz using thoses objects rather than fetching ngrams everytime)
	logger.info("["+str(e.id)+"] now updating tfidf")
	makeAllTfidf(e)
	logger.info("["+str(e.id)+"] tfidf sucessfully updated")
	
	if e.speaker_set.count()>0:
		makeViz(e,'Cloud_SolrSpeakerTagCloud')
		makeViz(e,'Graph_SpeakersSpeakers')
		makeViz(e,'Graph_SpeakersWords')
		makeViz(e,'Graph_SpeakersAttributes')
				
	e.status='0'
	e.save()
	logger.info("["+str(e.id)+"] import process done")

	d['status']='enquete imported'
	json = simplejson.dumps(d,indent=4,ensure_ascii=False)
	return HttpResponse(json, mimetype="application/json")
################################################################################










################################################################################
# ENQUETES
################################################################################
def eBrowse(request):
	enquetesandmeta=[]
	enquetes = Enquete.objects.all().order_by('-id')
	return render_to_response('bq_e_browse.html', {'bodyid':'e' ,'enquetes':enquetes}, context_instance=RequestContext(request))
################################################################################	



















################################################################################
def resetNgrams(request,eid):
	e = Enquete.objects.get(id=eid)
	logger.info("["+str(e.id)+"] fetching ngrams with tfidf - start...")
	makeAllTfidf(e)
	logger.info("["+str(e.id)+"] fetching ngrams with tfidf - done with success")
	return HttpResponse("tfidf updated", mimetype="application/json")
################################################################################
def eReset(request):
	d={}
	status=''
	folname = request.GET.get('foldname','')
	if folname=='':
		status='nothing done'
	else:
		try:
			shutil.rmtree(settings.REANALYSEUPLOADPATH+folname)
			status=folname+' successfully removed'
		except:
			status='failed to erase folder: '+folname
	d['status']=status
	json = simplejson.dumps(d,indent=4,ensure_ascii=False)
	return HttpResponse(json, mimetype="application/json")
################################################################################













###########################################################################
def eShow(request,eid):
	e = Enquete.objects.get(id=eid)
	meta = e.meta()
	
	metashort=[]
# 	metashort.append(['Study Author', meta['AuthEnty'][0]])
# 	metashort.append(['Funding Agency', meta['fundAg'][0]])
# 	metashort.append(['Country', meta['nation'][0]])
# 	metashort.append(['Geographic Coverage', meta['geogCover'][0]])
# 	metashort.append(['Data Distribution', meta['distrbtr'][0]])
# 	metashort.append(['Metadata', meta['AuthEnty'][0]+", Copyright  "+meta['copyright'][0]])

	for k in meta.keys():
		metashort.append([k,meta[k][0]])

	try:
		description	= meta['description'][0]
	except:
		description = "There wasn't any *description field in the meta_study.csv, sorry."
	
	ctx = {'bodyid':'e','pageid':'overview','enquete':e,'metashort':metashort,'description':description}
	updateCtxWithPerm(ctx,request,e)
	updateCtxWithSearchForm(ctx)
	return render_to_response('bq_e_show.html',ctx, context_instance=RequestContext(request))
###########################################################################
@login_required
def eseShow(request,eid):
	e = Enquete.objects.get(id=eid)
	ese = simplejson.loads(e.ese)
	ctx = {'bodyid':'e','pageid':'ese','enquete':e,'ese':ese}
	updateCtxWithPerm(ctx,request,e)
	updateCtxWithSearchForm(ctx)
	return render_to_response('bq_e_ese.html',ctx ,context_instance=RequestContext(request))
###########################################################################

	
	
	
	
	

###########################################################################
def getStrFromVizList(relViz):
	# css class vizinvloved is then made popup-able using js
	vizStr='<div style="display:none;">'+str(len(relViz))+'</div>'
	for v in relViz:
		vizStr+='<div title="'+v.name+'" class="vizinvolved viztype_'+v.viztype+'" id="viz_'+str(v.id)+'">'+str(v.id)+'</div>'
	return vizStr
###########################################################################
@login_required
@permission_required('reanalyseapp.can_browse')
def edBrowse(request,eid):
	# ENQUETE
	e = Enquete.objects.get(id=eid)
	only = Q(doccat='analyse')|Q(doccat='preparatory')|Q(doccat='verbatim')|Q(doccat='publication')
	only = only|Q(doccat='Analyse')|Q(doccat='Preparatory')|Q(doccat='Verbatim')|Q(doccat='Publication')
	textes = e.texte_set.filter(only)
	
	ctx = {'bodyid':'e','pageid':'documents','enquete':e}
	
	##################### DEPRECATED : django_tables2 (document list)
	#tTable = TextTable(textes,order_by=request.GET.get('sort'))
	#tTable.paginate(per_page=request.GET.get('per_pageT', 20), page=request.GET.get('page', 1))
	
	##################### NB: now we are building the table manually, filling columns and each cell value.. to use jquery datatable plugin (see template)
	tTable={}
	
	######################################### COLUMNS
	if request.user.has_perm('reanalyseapp.can_make'):
		colArr=['Category','Name','Size','Status','Viz','Investigator','Speakers'] #+ ['Length']
	else:
		colArr=['Category','Name','Size','Viz','Investigator','Speakers']
	
	
	######################################### VALUES
	valArr=[]
	
	rStyle='<span style="color:red;">'
	vStyle='<span style="color:green;">'
	bStyle='<span style="color:blue;">'
	endStyle='</span>'
	
	globalMaxLength = 1
	for t in textes:
		globalMaxLength = max(globalMaxLength, len(t.contenttxt))
		
	for t in textes:
		docDict={}
	
		eiddid = [e.id,t.id]
		# DOC NAME
		if t.doctype=='LNK':
			# external link
			linkDoc = t.locationpath
			nameStr = t.name + ' <a target="_new" href="'+linkDoc+'" onclick="event.stopPropagation();"><span class="imExternalLink"> </span></a>'
		else:
			# display link using edShow
			linkDoc = reverse(edShow,args=eiddid)
			nameStr = '<a href="'+linkDoc+'" onclick="event.stopPropagation();">'+t.name+'</a>'+' ('+t.get_doctype_display().lower()+')'

		# FILESIZE
		if t.filesize==0:
			sizeStr = '-'
		else:
			sizeStr = str(t.filesize)+" Ko"
		
		# DATA / STATUS / ACTIONS
		dataStr=""
		#linkParse = reverse(edParseXml,args=eiddid)
		#parseStr='<a href="" onclick=\'event.preventDefault();event.stopPropagation();doGetAtUrl("'+linkParse+'");return false;\'>parse</a>&nbsp;'
		#linkStyle = reverse(edStylizeContent,args=eiddid)
		#refreshStr='<a href="" onclick=\'event.preventDefault();event.stopPropagation();doGetAtUrl("'+linkStyle+'");return false;\'>stylize</a>&nbsp;'
		#if t.doctype=='TEI' or t.doctype=='CTX':
		#	dataStr += parseStr
		#if request.user.has_perm('reanalyseapp.can_make') and t.doctype=='TEI':
		#	dataStr += parseStr
		#if len(t.contenttxt)>0 and request.user.has_perm('reanalyseapp.can_make'):
		#	dataStr += vStyle +'parsed' + endStyle
		statusStr = t.get_status_display()
		if t.status!='0' and (t.doctype=='TEI' or t.doctype=='CTX') :
			statusStr += ' ' + str(t.statuscomplete) + '%'
		#if t.doctype=='TEI':
		#	linkDl = reverse(edXmlShow,args=eiddid)
		#	statusStr += ' <a href="'+linkDl+'">xhtml</a>'
		
		# VIZ
		vizStr = getStrFromVizList(getRelatedViz(textes=[t],user=request.user))
		
		# SPEAKERS
		if t.doctype=='TEI':
			nSpk = t.speaker_set.filter(ddi_type='SPK').count()
			if nSpk>1:
				speakersStr = str(nSpk)+" Speakers"
			else:
				speakersStr = ",".join([s.name for s in t.speaker_set.filter(ddi_type='SPK')])
			try:
				investStr = t.speaker_set.filter(ddi_type='INV')[0].name
			except:
				investStr=""
		else:
			speakersStr=""
			investStr=""
			
		# CONTENT (LENGTH)
		# simple viz experimentation to display length of verbatim, with colors for each spk involved
		if request.user.has_perm('reanalyseapp.can_make'):
			# d3 make it
			contentStr=\
				'<span style="display:none;">'+intToSortableStr(len(t.contenttxt))+'</span>'+\
				'<div id="d3_texte_'+str(t.id)+'" ></div>'
		else:
			# Simple div
			contentStr=\
				'<span style="display:none;">'+intToSortableStr(len(t.contenttxt))+'</span>'+\
				'<div class="littleFrise" style="width:'+str(int(len(t.contenttxt)*LITTLEFRISEMAXWIDTH/globalMaxLength))+'px"></div>'
		
		################# VALUES
		if request.user.has_perm('reanalyseapp.can_make'):
			tArr=[t.doccat,nameStr,sizeStr,statusStr,vizStr,investStr,speakersStr] #+ [contentStr]
		else:
			tArr=[t.doccat,nameStr,sizeStr,vizStr,investStr,speakersStr]
			
		docDict['texte']=t
		docDict['vals']=tArr
		valArr.append(docDict)
	
	tTable['columns']=colArr
	tTable['values']=valArr
	
	#################### Colors
	speakersColors = getSpeakersColorsDict(e,None)
	
	#################### AttributeTypes
	attributeTypes = AttributeType.objects.filter(enquete=e)
	colarray=[]
	for att in attributeTypes:
		coldict={'id':att.id, 'label':att.name}
		diffAtts = Attribute.objects.filter(attributetype=att).values('name')
		if len(diffAtts)<8:
			coldict['values']=diffAtts
		colarray.append(coldict)
	colarray.append({'label':'Textes'})
	
	ctx.update({'tTable':tTable, 'speakersColors':speakersColors, 'attributeTypes':colarray})
	updateCtxWithPerm(ctx,request,e)
	updateCtxWithSearchForm(ctx)
	if ctx['permexplorethis']:
		return render_to_response('bq_ed_browse.html', ctx, context_instance=RequestContext(request))
	else:
		return redirect(settings.LOGIN_URL) 
################################################################################
@login_required
@permission_required('reanalyseapp.can_browse')
def esBrowse(request,eid):
	# ENQUETE
	e = Enquete.objects.get(id=eid)
	
	#speakers = e.speaker_set.all()
	speakers = e.speaker_set.filter(ddi_type='SPK') # only real speakers : filter(ddi_type='SPK')
	
	attributeTypes = e.attributetype_set.filter(publicy='1') # only public attributes
	
	ctx = {'bodyid':'e','pageid':'speakers', 'enquete':e}
	
# 	speakerTableViz,isnew = Visualization.objects.get_or_create(enquete=enquete,name="speakerTable",description="",status='4')
# 	
# 	if isnew:

	#################### Handmade table with all speaker attributes
	
	# you may want to limit attributes, to test
	#attributeTypes = attributeTypes[:4]
	
	sTable={}
	#sTable['speakers']=speakers
	valArray=[]
	
	
	############################### COLUMNS
	# nb: color cells {'label':''} managed by javascript were commented
	if request.user.has_perm('reanalyseapp.can_make'):
		#colarray=[{'label':'Id'},{'label':''},{'label':'Name'},{'label':'Visualizations'},{'label':'Count'},{'label':'django_ngrams '+str(Ngram.objects.count())}]
		colarray=[{'label':'Name'},{'label':'<span class="imDocument"></span> Textes'},{'label':'Viz'},{'label':'Words'},{'label':'Ngrams'},{'label':'Id'}]
	else:
		colarray=[{'label':'Name'},{'label':'<span class="imDocument"></span> Textes'},{'label':'Viz'},{'label':'Words'}]
		
	for att in attributeTypes:
		coldict={'id':att.id, 'label':att.name}
		# NB: impossible to serialize ValuesQuerySet, so we make an array..
		diffAtts = [vvv for vvv in Attribute.objects.filter(attributetype=att).values('name')]
		if len(diffAtts)<8:
			coldict['values']=diffAtts
		colarray.append(coldict)
	
	
	############################### VALUES
	globalMaxCount = 1
	for sp in e.speaker_set.all():
		#globalMaxCount = max(globalMaxCount, sp.sentence_set.count())
		globalMaxCount = max(globalMaxCount, len(sp.contenttxt))
		
	for s in speakers:
		txtlen = len(s.contenttxt)
		
		nameLinkStr = \
			'<a onclick="event.stopPropagation();" href="'+ reverse(esShow,args=[eid,s.id]) +'">'+ s.name +'</a>'
		countStr = \
			'<span style="display:none;">'+intToSortableStr(txtlen)+'</span>'+\
			'<div class="littleFrise" style="width:'+str(txtlen*LITTLEFRISEMAXWIDTH/globalMaxCount)+'px"></div>'
			#' '+ str(txtlen)
		typeStr = \
			'<span style="display:none;">'+s.get_ddi_type_display()+'</span>'+\
			'<span class="imSpk'+s.get_ddi_type_display()+'"></span>'

		vizStr = getStrFromVizList(getRelatedViz(speakers=[s],user=request.user))
		
		
		ngramcount = str(s.ngramspeaker_set.count())
		#ngramjsonlink = ' <a href="'+ reverse(esGetSolrTermVector,args=[eid,s.id]) +'">json_ngrams</a>'
		ngramsStr = ngramcount
		
		## text presence list
		texteslist = [t for t in s.textes.all()]
		if len(texteslist)==1:
			tx = texteslist[0]
			linkDoc = reverse(edShow,args=[e.id,tx.id])
			nameStr = '<a href="'+linkDoc+'">'+tx.name+'</a>'
		else:
			nameStr = "in "+str(len(texteslist))+" docs"
					
		########################## VALUES
		if request.user.has_perm('reanalyseapp.can_make'):
			#vals = [s.id,s.color,nameLinkStr,vizStr,littleFriseStr,ngramsStr]
			vals = [nameLinkStr,nameStr,vizStr,countStr,ngramsStr,s.id]
		else:
			vals = [nameLinkStr,nameStr,vizStr,countStr]
						
		## one column for each attribute
		for k,attype in enumerate(attributeTypes):
			vals.append( s.attributes.get(attributetype=attype).name )
		
		########################## TO DICT
		valArray.append({'sid':s.id,'vals':vals})
	
	sTable['columns']=colarray
	sTable['values']=valArray
	
	#### Speaker Sets
	#speakersets = e.speakerset_set.all()
	#ctx.update({'speakersets':speakersets})
	#### Colors
	#speakersColors = getSpeakersColorsDict(e,None)	
	#ctx.update({'speakersColors':speakersColors})
	
	# sScrollXInner for datatables (because difficult to estimate width needed to put attributes
	# lets give 100px for each column
	ctx.update({'sScrollXInner':200+len(colarray)*100})
	
	ctx.update({'sTable':sTable,'attributeTypes':colarray})
	updateCtxWithPerm(ctx,request,e)
	updateCtxWithSearchForm(ctx)
	if ctx['permexplorethis']:
		return render_to_response('bq_es_browse.html', ctx , context_instance=RequestContext(request))
	else:
		return redirect(settings.LOGIN_URL) 
################################################################################
@login_required
@permission_required('reanalyseapp.can_browse')
def ewShow(request,eid,wid):
	# WORD
	we = WordEntity.objects.get(id=wid)
	e = we.code.enquete
	# custom dict
	stat=dict()
	
	# get only N MAX TFIDF in every wes
	N=20
	theWes = WordEntitySpeaker.objects.filter(speaker__enquete=e,wordentity=we).order_by('-tfidf')[:N]
	for wes in theWes:
		stat[wes]={'speaker':wes.speaker.name,'tfidf':'%.4f'%wes.tfidf}

	# get Textes using this word & how much for each Speaker
	#theWordEntitySpeakers = WordEntitySpeaker.objects.filter(speaker__enquete=e,wordentity=we)
	#stat['texts']=0
	ctx = {'word':we,'stat':stat}
	updateCtxWithPerm(ctx,request,e)
	updateCtxWithSearchForm(ctx)
	return render_to_response('bq_ew_show.html', ctx, context_instance=RequestContext(request))
#####################################################
@login_required
@permission_required('reanalyseapp.can_browse')
def dGetHtmlAround(request,eid,sid):
	sentence = Sentence.objects.get(id=sid)
	texte = sentence.texte
	
	sStart = sentence.i - 1
	sEnd = sentence.i + 1
	timeparts = getTextContent(texte,sStart,sEnd)
	
	ctx={'timeparts':timeparts}
	
	if request.GET.get('highlight'):
		ctx.update({'highlight':request.GET.get('highlight')})
		
	return render_to_response('bq_render_d.html', ctx, context_instance=RequestContext(request))
#####################################################
# return styled html of a portion of the text, using template
# todo: another solution: using XSLT from original TEI XML, this would be simpler without need to parse at the beginning!
@login_required
@permission_required('reanalyseapp.can_browse')
def dGetHtmlContent(request,eid,did):
	texte = Texte.objects.get(id=did)
	sStart = request.GET.get('from',0)
	sEnd = request.GET.get('to',0)
	timeparts = getTextContent(texte,sStart,sEnd)
	
	ctx={'timeparts':timeparts}
	
	if request.GET.get('highlight'):
		ctx.update({'highlight':request.GET.get('highlight')})
		
	return render_to_response('bq_render_d.html', ctx, context_instance=RequestContext(request))
###################################################################################################################################
@login_required
@permission_required('reanalyseapp.can_browse')
def edXmlShow(request,eid,did):
	# return HTML made by XSLT from XML file
	
	texte = Texte.objects.get(id=did)
	
	xml_file = texte.locationpath
	xslt_file = settings.MEDIA_ROOT + 'xml/txm2html.xsl'
	#xslt_file = settings.MEDIA_ROOT + 'xml/tei2html.xsl'
	
	########## A: using etree
	xml_root = etree.XML(open(xml_file, 'r').read())
	xslt_root = etree.XML(open(xslt_file, 'r').read())
	transform = etree.XSLT(xslt_root)
	result = etree.tostring(transform(xml_root))
	return HttpResponse(result)
	
	########## A: using libxslt
# 	xsl = libxslt.parseStyleSheetDoc(libxml2.parseFile( xslt_file ))
# 	data = libxml2.parseFile( xml_file )
# 	result = xsl.applyStylesheet(data)
# 	response = HttpResponse()
# 	xsl.saveResultToFile(response, result)
# 	return response
##########################################################
@login_required
@permission_required('reanalyseapp.can_browse')
def edShow(request,eid,did):

	###### ANY DOCUMENT
	e = Enquete.objects.get(id=eid)
	texte = Texte.objects.get(id=did)
	ctx = {'enquete':texte.enquete,'texte':texte,'bodyid':'e','pageid':'documents'}
	
	######################################### TEI
	if texte.doctype=='TEI':
	
		###### RELATED VIZ
		# we can take all related viz if we want
		#ctx.update({'visualizations':getRelatedViz(textes=[texte])})
		# now testing with only the textstreamtimeline
		try:
			streamtimelineviz = Visualization.objects.get(textes=texte,viztype='TexteStreamTimeline')
		except:
			try:
				streamtimelineviz = Visualization.objects.filter(textes=texte,viztype='TexteStreamTimeline')[0]
			except:
				streamtimelineviz = None
		ctx.update({'visualization':streamtimelineviz})
	
		maxTextPart = texte.sentence_set.aggregate(Max('i')).values()[0]
		
		if request.GET.get('highlight'):
			ctx.update({'highlight':request.GET.get('highlight')})
		
		if request.GET.get('around'):
			around = int(request.GET.get('around'))
			minPart = max(0,around-2)
			maxPart = min(maxTextPart,around+2)
		else:
			minPart = request.GET.get('from',0)
			maxPart = request.GET.get('to',maxTextPart)
			
		ctx.update({'minpart':minPart,'maxpart':maxPart,'totalmaxparts':maxTextPart})
		
		### CODES_PARAVERBAL DICT FOR LEGEND (see globalvars)
		ctx.update({'paraverbal':PARVBCODES})
		
		### CODES_TREETAGGER DICT FOR display
		ctx.update({'codes_treetagger':CODES_TREETAGGER})
		
		### COLORS FOR SPEAKERS
		speakersColors = getSpeakersColorsDict(e,texte)
		ctx.update({'speakersColors':speakersColors})
		
		### SPEAKERS
		inv = texte.speaker_set.filter(ddi_type="INV")
		spk = texte.speaker_set.filter(ddi_type="SPK")
		pro = texte.speaker_set.filter(ddi_type="PRO")
		ctx.update({'speakers':{'inv':inv,'spk':spk,'pro':pro}})
		
	######################################### CSV
	if texte.doctype=='CSV':
		columns=[]
		values=[]
		# Parse cvs and build table
		p = parseCsvFile(texte.locationpath)
		header = p['header']
		content = p['content']
		for h in header:
			columns.append(h)
		for lign in content:
			thevals=[]
			for att in header:
				if att.startswith("_"):
					cssClass="show"
				else:
					cssClass=""
				thevals.append([cssClass,lign[att]])
			values.append(thevals)
		ctx.update({'csvTable':{'columns':columns,'values':values}})
	#########################################
	
	updateCtxWithPerm(ctx,request,e)
	updateCtxWithSearchForm(ctx)
	if ctx['permexplorethis']:
		return render_to_response('bq_ed_show.html', ctx , context_instance=RequestContext(request))
	else:
		return redirect(settings.LOGIN_URL) 
###################################################################################################################################
@login_required
@permission_required('reanalyseapp.can_browse')
def esShow(request,eid,sid):
	speaker = Speaker.objects.get(id=sid)
	e = speaker.enquete
	
	ctx = {'enquete':e,'speaker':speaker,'bodyid':'e','pageid':'speakers'}
	
	### Speaker attributes
	attributeTypes = AttributeType.objects.filter(enquete=e)
	columns=[]
	values=[]
	for att in attributeTypes:
		if att.name != '_description':
			columns.append( att.name )
			values.append( speaker.attributes.get(attributetype=att).name  )
	attributes={'columns':columns,'values':values}
	
	### Speaker content with '\n' replaced by <p></p>
	content = makeHtmlFromText(speaker.contenttxt)
	
	### Speaker words stored in django DB (ie tfidf from solr)
	ngrams = speaker.ngramspeaker_set.order_by('-tfidf')
	
	ctx.update({'content':content,'attributes':attributes,'ngrams':ngrams})
	updateCtxWithPerm(ctx,request,e)
	updateCtxWithSearchForm(ctx)
	return render_to_response('bq_es_show.html', ctx , context_instance=RequestContext(request))
#####################################################
@login_required
@permission_required('reanalyseapp.can_browse')
def ecShow(request,eid,cid):
	e = Enquete.objects.get(id=eid)
	
	# CODE
	# object
	code = Code.objects.get(id=cid)
	# structured precalculated json
	stats=dict()
	stats['textes']=1
	stats['speaker']=1
	
	ctx = {'code':code,'stats':stats}
	updateCtxWithPerm(ctx,request,e)
	updateCtxWithSearchForm(ctx)
	return render_to_response('bq_ec_show.html',ctx, context_instance=RequestContext(request))
################################################################################







###########################################################################
# download link (for ESE report)
@login_required
def getEseReport(request,eid):
	e = Enquete.objects.get(id=eid)
	ese = simplejson.loads(e.ese)
	filepath = ese['reportpath']
	logger.info("Downloading ESE report:"+filepath)
	#pdfname = eseid + "_"+ese.report.split('/')[-1]
	pdfname = 'enquetesurenquete.pdf'
	response = HttpResponse(mimetype="application/pdf")
	response['X-Sendfile'] = filepath
	response['Content-Disposition'] = 'attachment; filename='+pdfname
	return response
###########################################################################
#The following view uses mod_xsendfile which lets apache do the file audio streaming for ESE
@login_required
def stream(request,eid,path):
	e = Enquete.objects.get(id=eid)
	# todo: to improve
	path='/'+path # (cause sent in the url first / doesnt appear)
	logger.info("Streaming audio file path:"+path)
	response = HttpResponse(mimetype="audio/mpeg")
	#response['Content-Disposition'] = 'filename=%s' % 'blarg.mp3'#smart_str(file_name)
	#Requires mod_xsendfile
	response['X-Sendfile'] = path	
	#response['Content-length'] = os.stat(path).st_size
	#return HttpResponse(path, mimetype='application/text')
	return response
###########################################################################
#This is the high overhead version which requires python to read and stream the file
#def stream2(request, enquete, path):
#	#note that MP3_STORAGE should not be in MEDIA_ROOT
#	path = '%s%s/%s' % (unicode(settings.REANALYSEESE_FILES), unicode(enquete),unicode(path))
#	filehandle = open(path,'rb')
#	#return HttpResponse("Enquete: "+enquete+ " Path: "+path) 
#	response = HttpResponse(FileWrapper(filehandle), mimetype="audio/mpeg")
#	response['Content-Length'] = os.path.getsize(path)
#	return response
###########################################################################








###########################################################################
# DOWNLOAD VIZ
###########################################################################
# as an attachment
def downloadGraph(request,gid):
	g = Visualization.objects.get(id=gid)
	response = HttpResponse(mimetype="application/xml")
	response['Content-Disposition'] = 'attachment; filename=%s.gexf' % g.name
	response['X-Sendfile'] = g.locationpath
	return response
###########################################################################
# serve file (used for sigma gexf flash)
def serveGraph(request,gid):
	g = Visualization.objects.get(id=gid)
	fsock = open(g.locationpath,"rb").read()
	response = HttpResponse(fsock, mimetype="text/plain")
	return response
# serve pdf (showing pdf files)
def servePdf(request,did):
	d = Texte.objects.get(id=did)
	fsock = open(d.locationpath,"rb").read()
	response = HttpResponse(fsock, mimetype="application/pdf")
	return response
###########################################################################


















###########################################################################
class MyFacetedSearchView(FacetedSearchView):
	def __name__(self):
		return "FacetedSearchView"
	
	# to add some things in the context
	def extra_context(self):
		extra = super(FacetedSearchView, self).extra_context()

		if self.results == []:
			extra['facets'] = self.form.search().facet_counts()
		else:
			extra['facets'] = self.results.facet_counts()
		
		return extra	
###########################################################################
# Let's try with haystack
@login_required
@permission_required('reanalyseapp.can_browse')
def eSearch(request,eid):
	############# To search from the shell you can do ;
# 	from haystack.query import SearchQuerySet
# 	from reanalyse.reanalyseapp.models import *
# 	sqs = SearchQuerySet().models(Sentence).filter(content__contains='je ne').highlight()
# 	res = sqs[0]
# 	print result.object.content
# 	print result.highlighted
	
	#sForm = FacetedModelSearchForm()
	#sForm = DateRangeSearchForm()
	
	e = Enquete.objects.get(id=eid)
	
	###### COLORS FOR SPEAKERS
	colors = dict()
	for s in e.speaker_set.all():
		colors[int(s.id)]=s.color
	ctx={'enquete':e,'colors':colors}
	
	###### we get all parameters in the form
	que = request.GET.get('q', '')
	searchonly = request.GET.get('searchonly', '')
	sortby = request.GET.get('sortb', '')
	# faceting by hand
	inTextesIds = request.GET.get('inTextes', '')
	inSpeakersIds = request.GET.get('inSpeakers', '')
	rawQuery = request.GET.get('rawQuery','')
	autocomplete = request.GET.get('autocomplete','')
	autocompletew = request.GET.get('autocompletew','')
	
	##########################################################################################
	if rawQuery=='on':
		# raw query
		###### TRYING REGULAR EXPRESSIONS & PROXIMITY with raw_queries !
		#SearchQuerySet().raw_search('django_ct:blog.blogentry "However, it is"')
		# LUCENE SUPPORTS
		# "je ne"~20			2 words within distance < 20
		# "jakarta^4 apache"	boost jakarta
		# "te?t", "test*"		warning: doesnt work if first character
		# "word~" "word~0.9"	fuzzy search with/without parameter
		#sqs = SearchQuerySet().raw_search('django_ct:intervention '+que)
		"""
		###### SEE: http://wiki.apache.org/solr/FunctionQuery
		###### get frequency of aword
		q={!func}docfreq(text,'aword')
		###### returns the number of times the term appears in the field in the entire index. ttf is an alias of totaltermfreq.
		q={!func}ttf(text,'aword')
		###### get all results and sort using a function
		q=*:*&sort=dist(2, point1, point2) desc
		q=*:*&sort=max(2, point1, point2) desc
		q=user_query:"i"&wt=json&fl=user_query&indent=on&echoParams=none&rows=10&sort=count desc
		q=user_query:"pour"&wt=json&fl=user_query&indent=on&echoParams=none&rows=10&sort=count desc
		"""
		#sqs = SearchQuerySet().raw_search('{!func}docfreq(text,'+que+')')
		#sqs = SearchQuerySet().raw_search(que+'&sort=speaker_exact')
		sqs = SearchQuerySet().raw_search(que)
		#ctx['log']=sqs[0].object.content
		#sqs = SearchQuerySet().raw_search(que)
	##########################################################################################
	else:
		logger.info("search query:"+que)
		
		#################################### FACETING MENU LIST
		#
		# TODO: maybe more efficient if using raw_queries & facets (like q=speaker:12&facet.field=...)
		#
		# getting, for each Texte/Speaker:
		# 0.number of interventions for the query 'q'
		# 1.total number of interventions 
		# 2.name
		# 3.is currently selected ?
		# ... then, we could sort the arrays ! (to display biggest actors for a specific query)
		handfacets={}
		tFacets=[]
		sFacets=[]
		maxT=0
		maxS=0
		############ ALSO fill the d3data dict (to build the graph with Textes/Speakers links)
		#d3data={}
		involvedTextes=[]
		involvedSpeakers=[]
		#dLinks=[]
		
		##### BEFORE JC, WE DID IT BY HAND, querying each facet and getting ".count()" : THIS IS VERY HEAVY
# 		for t in Texte.objects.filter(enquete=e,doctype='TEI'):
# 			ntotal = t.intervention_set.count()
# 			maxT = max(ntotal,maxT)
# 			nres = sqs.filter( texteid=t.id, content__contains=que ).count()
# 			tFacets.append([ nres,ntotal , t, str(t.id) in inTextesIds ])
# 			if nres>0:
# 				dTextes.append(t)
# 		for s in Speaker.objects.filter(enquete=e):
# 			ntotal = s.intervention_set.count()
# 			maxS = max(ntotal,maxS)
# 			if ntotal>0:
# 				nres = sqs.filter( speakerid=s.id, content__contains=que ).count()
# 				sFacets.append([ nres, ntotal, s, str(s.id) in inSpeakersIds ])
# 				if nres>0:
# 					dSpeakers.append(s)
# 					for t in s.textes.all():
# 						if t in dTextes:
# 							linkT=dTextes.index(t)
# 							linkS=dSpeakers.index(s)
# 	 							dLinks.append([linkT,linkS])
# 		handfacets['textes']=tFacets
# 		handfacets['speakers']=sFacets
# 		handfacets['maxT']=maxT
# 		handfacets['maxS']=maxS
#		ctx['handfacets']=handfacets
				
		sqs = SearchQuerySet().models(Sentence).filter(enqueteid=eid)
		
# 		#################################### FACET-RESULTS by Texte/Speaker
# 		# cool with SQ() 'custom' facets, allowing to filter using AND/OR operators)
# 		if len(inSpeakersIds)>0:
# 			or_query = None
# 			for sid in inSpeakersIds.split(','):
# 				if or_query is None:
# 					or_query = SQ(speakerid=sid)
# 				else:
# 					or_query = or_query | SQ(speakerid=sid)
# 			sqs = sqs.filter(or_query)
# 		if len(inTextesIds)>0:
# 			or_query = None
# 			for tid in inTextesIds.split(','):
# 				if or_query is None:
# 					or_query = SQ(texteid=tid)
# 				else:
# 					or_query = or_query | SQ(texteid=tid)
# 			sqs = sqs.filter(or_query)
		
		
		#################################### todo: AUTOCOMPLETE - not tried
		# todo: AJAX call to get 3/4 examples of results (autocomplete) ?
# 		if autocomplete=='on':
# 			sqs = sqs.autocomplete(content_c_auto=que)
# 		if autocompletew=='on':
# 			sqs = sqs.autocomplete(content_w_auto=que)
		# not working?
		#sqs = sqs.filter(texte__enquete=e)
		
	####### SPELLING SUGGESTION - NOT WORKING YET...
	#suggestion = SearchQuerySet().filter(content_auto=que).spelling_suggestion()
	#suggestion='no suggestion'
	
	####### GET SOME MANUAL SUGGESTIONS by getting nb of results for sub-queries
# 	spited = que.split(" ")
# 	d={}
# 	if len(spited)>1:
# 		if len(spited)==2: # A,B
# 			for u in spited:
# 				n = SearchQuerySet().models(Intervention).filter(content__contains=u).count()
# 				d[u]=n
# 		elif len(spited)==3: # we test AB,BC
# 			for k in range(2):
# 				u=" ".join(spited[k:k+2])
# 				n = SearchQuerySet().models(Intervention).filter(content=u).count()
# 				d[u]=n
# 		else: # we test all ABC,BCD ... (len=3)
# 			for k in range(len(spited)-2):
# 				u=" ".join(spited[k:k+3])
# 				n = SearchQuerySet().models(Intervention).filter(content=u).count()
# 				d[u]=n
# 		ctx['youcould']=d
	
	
	####### HIGHLIGHT RESULTS
	# highlighting can be done in the template ({% highlight ...})
		# {% highlight r.object.content with query html_tag "div" css_class "result_highlighted" %} 
	# or in the view (faster):
		#sqs = sqs.highlight()
	
	
	################################# RELATED VISUALIZATIONS #################################
	# do not create viz here ! (time consuming!)
	#involvedAttributesViz = makeViz(e,'Attributes',textes=involvedTextes,speakers=involvedSpeakers)
	#ctx['visualizations']=getRelatedViz(textes=involvedTextes,speakers=involvedSpeakers)
	
	################################# BUILDING THE VIEW #################################
	
	####### BEFORE JC : works well but ...  how to add things in the context ? FACETS OK
	#search_view = MyFacetedSearchView(template='search.html', form_class=MyFacetedSearchForm, searchqueryset=sqs)
	#return search_view(request)
	
	####### BEFORE JC : well except without facets
	#return basic_search(request, template='search.html', load_all=False, form_class=MyFacetedSearchForm, searchqueryset=sqs, context_class=RequestContext, extra_context=moreDict, results_per_page=50)

	####### so... DOING IT BY HAND (FACETS OK now, since we updated the kwargs dict in the Form class)
	# because basic_search seems not to take into account ; extra_context (with facets)
	results = EmptySearchQuerySet()
	
	# MORE LIKE THIS & SUGGESTION (warning: to activate in solrconfig.xml !)
	# see http://django-haystack.readthedocs.org/en/latest/installing_search_engines.html
	#morelikethis = sqs.more_like_this([djangoObject])
	#suggestion = sqs.spelling_suggestion()
	
	sqs = sqs.highlight()

	try:
		overviewViz=e.visualization_set.filter(viztype='Overview')[-1]
	except:
		overviewViz=makeViz(e,'Overview')
	ctx['overviewViz']=overviewViz
					
	if request.GET.get('q'):
		form = SentenceSearchForm(request.GET, searchqueryset=sqs, load_all=False)
		if form.is_valid():
			query = form.cleaned_data['q']
			results = form.search()
			################################# SORT BY
			if sortby=='texte':
				results = results.order_by('texte')
			if sortby=='speaker':
				results = results.order_by('speaker')
			################################# FACET OVERVIEW VISUALIZATION
			##### AFTER JC, RATHER do it using haystack, faceting using .facet('texteid') or .facet('speakerid')
			# NB: (This is only to get general (xx/xx) counts for each text/speaker)
			facets = results.facet('speakerid').facet('texteid')
			## get textes facet_counts
			fcounts = facets.facet_counts()
			overviewStats={}
			if 'fields' in fcounts.keys():
				fieldCounts = fcounts['fields']
				## get textes facet_counts ...
	 			#for tup in fieldCounts['texteid']:
				## get speakers facet_counts
				for tup in fieldCounts['speakerid']:
					s=Speaker.objects.get(id=tup[0])
					nres=tup[1]
					#ntotal = s.sentence_set.count()
					#sFacets.append([ nres, ntotal , s, tup[0] in inSpeakersIds.split(',') ])
					overviewStats[s.id]=nres
					if nres>0:
						involvedSpeakers.append(s)
	# 						for t in s.textes.all():
	# 							if t in involvedTextes:
	# 								linkT=involvedTextes.index(t)
	# 								linkS=involvedSpeakers.index(s)
	# 								dLinks.append([linkT,linkS])
			
			ctx['overviewStats']=overviewStats
			#ctx['textes']=involvedTextes
			#ctx['speakers']=involvedSpeakers	
			
	else:
		form = SentenceSearchForm(searchqueryset=sqs, load_all=False)
	
	paginator = Paginator(results, 50)
	
	try:
		page = paginator.page(int(request.GET.get('page', 1)))
	except InvalidPage:
		raise Http404("No such page of results!")
	
	formcontext = {
		'form': form,
		'page': page,
		'paginator': paginator,
		'query': que,
		#'morelikethis': morelikethis,
		#'suggestion': suggestion,
	}
	
	########### EXTRA CONTEXT
	# now,
	# facets-counts are made by hand (already in the dict are done
	# faceted-results are filtered using sqs.facet('texteid() - see above

# 	if results == []:
# 		extra_context['facets'] = form.search().facet_counts()
# 	else:
# 		extra_context['facets'] = results.facet_counts()
	
	ctx.update( formcontext )
	ctx.update({'bodyid':'e','pageid':'search'})
	
	updateCtxWithPerm(ctx,request,e)

	return render_to_response('bq_e_searchresults.html', ctx, context_instance=RequestContext(request))
###########################################################################















###########################################################################
# DEPRECATED MAKE TAG CLOUD FROM FORM PARAMETERS
# def makeTagCloud(request,eid):
# 	# get ajax-form-parameters and build tagcloud
# 	e = Enquete.objects.get(id=eid)
# 	param=dict()
# 	param['count'] = request.GET['count']	# number of words to keep
# 	param['how'] = request.GET['how']		# sort by tfidf/freq
# 	# WHERE
# 	param['where']=request.GET['where_text_id'] # search in all texts("all")/only current
# 	# WHO
# 	param['who']=request.GET['who_speaker_id']	# search in all speakers("all")/only
# 
# 	d = makeTagCloudFrom(e,param)
# 	jsondata = simplejson.dumps(d,indent=4,ensure_ascii=False)
# 	return HttpResponse(jsondata, mimetype="application/json")
###########################################################################
# DEPRECATED INCLUDED IN VISUALIZATION
# def edGetJson(request,eid,did):
# 	texte = Texte.objects.get(id=did)
# 	dict = getStatDict(texte)
# 	json = simplejson.dumps(dict,indent=4,ensure_ascii=False)
# 	return HttpResponse(json, mimetype="application/json")
###########################################################################
# DEPRECATED INCLUDED IN VISUALIZATION
# def eGetJson(request,eid):
# 	enquete = Enquete.objects.get(id=eid)
# 	dict = getEnqueteTextsStatDict(enquete)
# 	json = simplejson.dumps(dict,indent=4,ensure_ascii=False)
# 	return HttpResponse(json, mimetype="application/json")
###########################################################################







###########################################################################
def setColor(request,eid):
	e = Enquete.objects.get(id=eid)
	newcolor = request.GET.get('color','')
	speakers = request.GET.get('speakers','').split(",")
	
	if speakers!=[''] and newcolor:
		for sid in speakers:
			s = Speaker.objects.get(id=sid)
			s.color = '#'+newcolor
			s.save()
	return HttpResponse("color updated", mimetype="application/json")		
###########################################################################
# put random colors on each speaker, base on HTML_COLORS list
def resetColors(request,eid):
	e = Enquete.objects.get(id=eid)
	randomizeSpeakersColors(e)
	return HttpResponse(simplejson.dumps({'status':'colors randomized'}), mimetype="application/json")		
###########################################################################









###########################################################################
# SPEAKER SETS
###########################################################################
@login_required
def makeSpeakerSet(request,eid):
	e = Enquete.objects.get(id=eid)
	
	speakerIds = request.GET.get('speakers','').split(",")
	name = request.GET.get('name','setNameNotSet')

	newSet = SpeakerSet(enquete=e,name=str(name),description='nodescryet')
	newSet.save()
	for sid in speakerIds:
		newSet.speakers.add( Speaker.objects.get(id=sid) )
	newSet.save()
	
	d={}
	d['speakersetid']=newSet.id
	json = simplejson.dumps(d,indent=2,ensure_ascii=False)
	return HttpResponse(json, mimetype="application/json")		
###########################################################################
def deleteSpeakerSets(request,eid):
	e = Enquete.objects.get(id=eid)
	for spks in e.speakerset_set.all():
		spks.delete()
	return HttpResponse("speakersets deleted", mimetype="application/json")
###########################################################################
def getSpeakerSetSpeakers(request,eid,ssid):
	e = Enquete.objects.get(id=eid)
	ss = SpeakerSet.objects.get(enquete=e,id=ssid)
	d={}
	d['speakersIds'] = [s.id for s in ss.speakers.all()]
	json = simplejson.dumps(d,indent=2,ensure_ascii=False)
	return HttpResponse(json, mimetype="application/json")
###########################################################################







###########################################################################
# VISUALIZATIONS
###########################################################################
# Browse Visualizations
@login_required
def evBrowse(request,eid):
	e = Enquete.objects.get(id=eid)
	speakersColors = getSpeakersColorsDict(e,None) # get all colors
	#visualizations = e.visualization_set.order_by('viztype','-id')
	visualizations = e.visualization_set.exclude(viztype='Overview').order_by('viztype','-id')
	
	ctx={'bodyid':'e','pageid':'visualizations','enquete':e,'visualizations':visualizations,'speakersColors':speakersColors,'viztypes':VIZTYPES}
	
	try:
		overviewViz=e.visualization_set.filter(viztype='Overview')[0]
	except:
		overviewViz=makeViz(e,'Overview')
	ctx['overviewViz']=overviewViz
	
	#### overviewStats is simple dic[spkId]=weight
	overviewStats={}
	for s in e.speaker_set.all():
		overviewStats[s.id]=e.visualization_set.filter(speakers=s).count()
	ctx['overviewStats']=overviewStats
	
	updateCtxWithPerm(ctx,request,e)
	updateCtxWithSearchForm(ctx)
	return render_to_response('bq_ev_browse.html',ctx, context_instance=RequestContext(request))
###########################################################################
# Delete Visualizations
def evDelete(request,eid,vid):
	v = Visualization.objects.get(enquete__id=eid,id=vid)
	v.delete()
	return HttpResponse("killed", mimetype="application/json")
###########################################################################
# Switch public/private status
def evSetPublic(request,eid,vid):
	v = Visualization.objects.get(enquete__id=eid,id=vid)
	v.public = not v.public
	v.save()
	json = simplejson.dumps({'status':v.public},indent=2,ensure_ascii=False)
	return HttpResponse(json, mimetype="application/json")
###########################################################################
def evGetJson(request,eid,vid):
	v = Visualization.objects.get(enquete__id=eid,id=vid)
	return HttpResponse(v.json, mimetype="application/json")
###########################################################################
def evSaveHtml(request,eid,vid):
	v = Visualization.objects.get(enquete__id=eid,id=vid)
	thehtml = request.POST
	v.contenthtml = thehtml
	v.save()
	return HttpResponse("done", mimetype="application/json")
###########################################################################
def evUpdateDescr(request,eid,vid):
	v = Visualization.objects.get(enquete__id=eid,id=vid)
	plaintext = request.POST['html']
	plainhtml = makeReturnsToHtml(plaintext)
	v.description = plainhtml
	v.save()
	json = simplejson.dumps({'html':plainhtml},indent=2,ensure_ascii=False)
	return HttpResponse(json, mimetype="application/json")
###########################################################################
# MAKE VISUALIZATION
@login_required
def makeVisualization(request,eid):
	e = Enquete.objects.get(id=eid)
	
	typ = request.GET.get('type','')
	
	speakerIds = request.GET.get('speakers','').split(",")
	texteIds = request.GET.get('textes','').split(",")
	attributetypesIds = request.GET.get('attributetypes','').split(",")
	
	if speakerIds==['']:
		speakers=[]
	else:
		speakers=[Speaker.objects.get(id=sid) for sid in speakerIds]
		
	if texteIds==['']:
		textes=[]
	else:
		textes=[Texte.objects.get(id=tid) for tid in texteIds]
		
	if attributetypesIds==['']:
		attributetypes=[]
	else:
		attributetypes=[AttributeType.objects.get(id=aid) for aid in attributetypesIds]
				
	count = int(request.GET.get('count','0'))
	
	newVizu = makeViz(e,typ,speakers=speakers,textes=textes,attributetypes=attributetypes,count=count)
		
	return HttpResponse(newVizu.json, mimetype="application/json")
###########################################################################
def getVizHtml(request,eid):
	e = Enquete.objects.get(id=eid)
	vId = request.GET.get('vizid',0)
	v = Visualization.objects.get(id=vId)
	ctx = {'enquete':e,'v':v,'nk':vId}
	t = loader.get_template('bq_render_v.html')
	d={}
	d['html'] 			= t.render(Context(ctx))
	d['description'] 	= v.description
	return HttpResponse(simplejson.dumps(d,indent=4,ensure_ascii=False), mimetype="application/json")
###########################################################################







	
	
	
###########################################################################
# AJAX/JSON ACTIONS/GET (todo: clean and remove deprecated!)
###########################################################################
def getLittleFriseJson(request,eid,tid):
	t = Texte.objects.get(id=tid)
	d = getDictLittleSpeakerSizesInText(t)
	jsondata = simplejson.dumps(d,indent=4,ensure_ascii=False)
	return HttpResponse(jsondata, mimetype="application/json")
###########################################################################
# def eiGetExtractHtml(request,eid,iid):
# 	i = Intervention.objects.get(id=iid)
# 	d={}
# 	d['html']=getHtmlAroundIntervention(i)
# 	jsondata = simplejson.dumps(d,indent=4,ensure_ascii=False)
# 	return HttpResponse(jsondata, mimetype="application/json")
###########################################################################







# SOLR INDEX
###########################################################################
def eSolrIndexClear(request):
	arg = {'interactive':False,'verbosity':0}
	clear_index.Command().handle(**arg)
	return HttpResponse('solr index cleared', mimetype="application/json")
###########################################################################
def eSolrIndexUpdate(request):
	update_index.Command().handle(verbosity=0)
	return HttpResponse('solr index updated', mimetype="application/json")
###########################################################################





# USEFUL
###########################################################################
def esGetSolrTermVector(request,eid,sid):
	speaker = Speaker.objects.get(id=sid)
	res = getSolrTermVectorsDict([speaker],'ngrams',count=0,mintn=3)
	json = simplejson.dumps(res,indent=4,ensure_ascii=False)
	return HttpResponse(json, mimetype="application/json")
###########################################################################





###########################################################################
def exportEnquetes(request):
	logger.info("exporting all Enquetes to XML")
	exportEnquetesAsXML()
	return render_to_response('bq_e_browse.html', context_instance=RequestContext(request))
###########################################################################
def deleteEnquetes(request):
	Enquete.objects.all().delete()
	return render_to_response('bq_e_browse.html', context_instance=RequestContext(request))
###########################################################################
# class ParseThread(threading.Thread):
# 	texte = None
# 	def setText(self,txt):
# 		self.texte=txt
# 	def run(self):
# 		self.texte.parseXml()
####################
def edParseXml(request,eid,did):
	texte = Texte.objects.get(id=did)
	texte.parseXml()
#	t = ParseThread()
#	t.setText(texte)
#	t.setDaemon(True)
#	t.start()
	d={}
	d['stats']={'nsentences':texte.sentence_set.count(),'nspeakers':texte.speaker_set.count()}
	jsondata = simplejson.dumps(d,indent=4,ensure_ascii=False)
	return HttpResponse(jsondata, mimetype="application/json")
###########################################################################
@login_required
def getTextPart(request):
	# JSON : DO IT YOURSELF
	jsondata = getEnquetePart()
	# RETURN RAW JSON 
	return HttpResponse(jsondata, mimetype="application/json")
def getJsonData(request,eid,data):
	e = Enquete.objects.get(id=eid)
	jsondata=None
	if data=='statenquete':
		# ajax-asked for processingjs
		docu=dict()
		docu['ids']=[]
		docu['names']=[]
		docu['nwords']=[]
		for t in e.texte_set.all():
			nWords=0
#			for s in t.speaker_set.all():
#				nWords += s.wordentityspeaker_set.filter(
#			WordEntity.objects.filter(code.)
			docu['ids'].append(t.id)
			docu['names'].append(t.name)
			docu['nwords'].append(nWords)
		jsondata = simplejson.dumps(docu,indent=4,ensure_ascii=False)
	elif data=='':
		a=0
	return HttpResponse(jsondata, mimetype="application/json")
###########################################################################













# DEPRECATED: upload part
################################################################################
# 	if request.method == 'GET':
# 		if len(request.FILES.getlist('thefiles'))>0:
# 			# create upload folder
# 			newFolderPath = settings.REANALYSEUPLOADPATH + str(time())
# 			os.mkdir(newFolderPath)
# 			#logger.info( "///////therequest :"+request.POST )
# 			#logger.info( "///////thefiles :"+request.FILES.getlist )
# 			#uform = UploadFileForm(request.POST, request.FILES)
# 			for f in request.FILES.getlist('thefiles'):
# 				logger.info( "UPLOADING FILE: "+newFolderPath+"/"+f.name )
# 				# not uploading for real, just get the right file in upload folder
# 				destination = open(newFolderPath+"/"+f.name, 'wb+')
# 				for chunk in f.chunks():
# 					destination.write(chunk)
# 				destination.close()
# 			# every file has been uploaded, then create enquete
# 			st = "from POST"
# 			#importEnqueteDDI2(newFolderPath+"/"+"ddi.xml")
# 		else:
# 			st = "from already xml"
# 			#importEnqueteDDI2(settings.REANALYSEUPLOADPATH+"PolitiqueEtEurope/PolitiqueEtEurope.xml")
# 	d={}
# 	d['message']=st
# 	d['success']='true'
# 	d['error']='pas vraiment de raison'+st
# 	jsondata = simplejson.dumps(d,indent=4,ensure_ascii=False)
# 	return HttpResponse(jsondata, mimetype="application/json")
###########################################################################










###########################################################################
# DEPRECATED : to dispay raw html content of file
# def docShow(request):
# 	filepath = settings.REANALYSESITECONTENTPATH + 'doc' + '_content.html'
# 	contenthtml = getContentOfFile(filepath)
# 	sc,isnew = SiteContent.objects.get_or_create(name='doc',contenthtml=contenthtml)
# 	return render_to_response('bq_home_doc.html', {'conten.html':sc.contenthtml}, context_instance=RequestContext(request))
################################################################################













# DEPRECATED
###########################################################################
# if you need to get html content of file, can be useful for raw content (about pages)
#def getHtml(request,templateName):
#	htmlfile=open(templateName+".html",'r')
#	html=''.join(htmlfile.readlines())
#	return HttpResponse(html, mimetype="text/plain")
#	return render_to_response(templateName+".html")
#	return HttpResponse(getIntroHtmlAsJson(templateName), mimetype="application/json")
###########################################################################
# def getWordStat(request,eid,wid):
# 	e = Enquete.objects.get(id=eid)
# 	w = WordEntity.objects.get(id=wid)
# 	# get tfidf of all words
# 	d = getStat(e)
# 	# get tfidf of selected word
# 	d['sel_ndoc'] = w.maxtfidf
# 	d['sel_tfidf'] = w.textes.count()
# 	d['sel_name'] = w.content
# 	jsondata = simplejson.dumps(d,indent=4,ensure_ascii=False)
# 	return HttpResponse(jsondata, mimetype="application/json")
###########################################################################
# def makeAction(request,eid,cmd):
# 	e = Enquete.objects.get(id=eid)
# 	d = dict()
# 	if cmd=='tfidf':
# 		calculateTfidf(e)
# 	if cmd=='get':
# 		d = getStat(e)
# 	jsondata = simplejson.dumps(d,indent=4,ensure_ascii=False)
# 	return HttpResponse(jsondata, mimetype="application/json")
###########################################################################













###########################################################################
# def testMe(request):
# 	if request.POST:
# 		content=request.POST
# 		status=ok
# 	else:
# 		content=status='none'
# 	d={'status':status,'content':content}
# 	jsondata = simplejson.dumps(d,indent=4,ensure_ascii=False)
# 	return HttpResponse(jsondata, mimetype="application/json")
###########################################################################