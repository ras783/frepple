#
# Copyright (C) 2007-2012 by Johan De Taeye, frePPLe bvba
#
# This library is free software; you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero
# General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

# file : $URL$
# revision : $LastChangedRevision$  $LastChangedBy$
# date : $LastChangedDate$

r'''
This module implements a generic view to presents lists and tables.

It provides the following functionality:
 - Pagination of the results.
 - Ability to filter on fields, using different operators.
 - Ability to sort on a field.
 - Export the results as a CSV file, ready for use in a spreadsheet.
 - Import CSV formatted data files.
 - Show time buckets to show data by time buckets.
   The time buckets and time boundaries can easily be updated.
'''

from datetime import date, datetime
from decimal import Decimal
import csv, cStringIO
import operator
import math
import locale
import codecs
import json
          
from django.conf import settings
from django.views.decorators.csrf import csrf_protect
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction, models
from django.db.models.fields import Field, CharField, AutoField
from django.db.models.fields.related import RelatedField
from django.http import Http404, HttpResponse, HttpResponseRedirect, HttpResponseForbidden, HttpResponseNotAllowed
from django.forms.models import modelform_factory
from django.shortcuts import render
from django.utils import translation, simplejson
from django.utils.decorators import method_decorator
from django.utils.encoding import smart_str, iri_to_uri, force_unicode
from django.utils.html import escape
from django.utils.translation import ugettext as _
from django.utils.formats import get_format, number_format
from django.utils import simplejson as json
from django.utils.text import capfirst, get_text_list
from django.utils.translation import string_concat
from django.contrib.admin.models import LogEntry, CHANGE, ADDITION, DELETION
from django.contrib.contenttypes.models import ContentType
from django.views.generic.base import View

from freppledb.common.models import Parameter, BucketDetail, Bucket, Comment

import logging
logger = logging.getLogger(__name__)


class GridField(object):
  '''
  Base field for columns in grid views.
  '''

  def __init__(self, name, **kwargs):
    self.name = name
    for key in kwargs:
      setattr(self, key, kwargs[key])
    if 'key' in kwargs: self.editable = False
    if not 'title' in kwargs: 
      self.title = self.name and _(self.name) or ''      
    if not self.name: 
      self.sortable = False
      self.search = False
    if not 'field_name' in kwargs: 
      self.field_name = self.name

  def __unicode__(self):
    o = [ "name:'%s',index:'%s',editable:%s,label:'%s',width:%s,align:'%s',title:false" % 
          (self.name or '', self.name or '', self.editable and "true" or "false", 
           force_unicode(self.title).title().replace("'","\\'"), 
           self.width, self.align
           ), ]
    if self.key: o.append( ",key:true" )
    if not self.sortable: o.append(",sortable:false")
    if not self.search: o.append(",search:false")
    if self.formatter: o.append(",formatter:'%s'" % self.formatter)
    if self.unformat: o.append(",unformat:'%s'" % self.unformat)
    if self.searchrules: o.append(",searchrules:{%s}" % self.searchrules)
    if self.extra: o.append(",%s" % force_unicode(self.extra))
    return ''.join(o)

  name = None
  field_name = None
  formatter = None
  width = 100
  editable = True
  sortable = True
  search = True
  key = False
  unformat = None
  title = None
  extra = None
  align = 'center'
  searchrules = None


class GridFieldDateTime(GridField):
  formatter = 'date'
  extra = "formatoptions:{srcformat:'Y-m-d H:i:s',newformat:'Y-m-d H:i:s'}"
  width = 140

  
class GridFieldTime(GridField):
  formatter = 'time'
  extra = "formatoptions:{srcformat:'H:i:s',newformat:'H:i:s'}"
  width = 80


class GridFieldDate(GridField):
  formatter = 'date'
  extra = "formatoptions:{srcformat:'Y-m-d H:i:s',newformat:'Y-m-d'}"
  width = 140


class GridFieldInteger(GridField):
  formatter = 'integer'
  width = 70
  searchrules = 'integer:true'


class GridFieldNumber(GridField):
  formatter = 'number'
  width = 70
  searchrules = 'number:true'


class GridFieldBool(GridField):
  extra = "formatoptions:{disabled:false}, edittype:'checkbox', editoptions:{value:'True:False'}"
  width = 60


class GridFieldLastModified(GridField):
  formatter = 'date'
  extra = "formatoptions:{srcformat:'Y-m-d H:i:s',newformat:'Y-m-d H:i:s'}"
  title = _('last modified')
  editable = False
  width = 140


class GridFieldText(GridField):
  width = 200
  align = 'left'


class GridFieldChoice(GridField):
  width = 100  
  align = 'center'
  def __init__(self, name, **kwargs):
    super(GridFieldChoice,self).__init__(name, **kwargs)
    e = ["formatter:'select', edittype:'select', editoptions:{value:'"]
    first = True
    for i in kwargs["choices"]:
      if first:
        first = False
        e.append("%s:" % i[0])
      else:
        e.append(";%s:" % i[0])
      e.append(i[1])
    e.append("'}")
    self.extra = string_concat(*e)
    
    
class GridFieldCurrency(GridField):   
  formatter = 'currency'
  extra = "formatoptions:{prefix:'%s', suffix:'%s'}"  % settings.CURRENCY
  width = 80

    
def getBOM(encoding):
  try: 
    # Get the official name of the encoding (since encodings can have many alias names)
    name = codecs.lookup(encoding).name  
  except:
    return ''  # Unknown encoding, without BOM header
  if name == 'utf-32-be': return codecs.BOM_UTF32_BE
  elif name == 'utf-32-le': return codecs.BOM_UTF32_LE
  elif name == 'utf-16-be': return codecs.BOM_UTF16_BE
  elif name == 'utf-16-le': return codecs.BOM_UTF16_LE
  elif name == 'utf-8': return codecs.BOM_UTF8
  else: return ''
  
  
class UTF8Recoder:
  """
  Iterator that reads an encoded data buffer and re-encodes the input to UTF-8.
  """
  def __init__(self, data):
    # Detect the encoding of the data by scanning the BOM. 
    # Skip the BOM header if it is found.
    if data.startswith(codecs.BOM_UTF32_BE): 
      self.reader = codecs.getreader('utf_32_be')(cStringIO.StringIO(data))
      self.reader.read(1)      
    elif data.startswith(codecs.BOM_UTF32_LE): 
      self.reader = codecs.getreader('utf_32_le')(cStringIO.StringIO(data))
      self.reader.read(1)      
    elif data.startswith(codecs.BOM_UTF16_BE): 
      self.reader = codecs.getreader('utf_16_be')(cStringIO.StringIO(data))
      self.reader.read(1)      
    elif data.startswith(codecs.BOM_UTF16_LE): 
      self.reader = codecs.getreader('utf_16_le')(cStringIO.StringIO(data))
      self.reader.read(1)      
    elif data.startswith(codecs.BOM_UTF8): 
      self.reader = codecs.getreader('utf-8')(cStringIO.StringIO(data))
      self.reader.read(1)      
    else:       
      # No BOM header found. We assume the data is encoded in the default CSV character set.
      self.reader = codecs.getreader(settings.CSV_CHARSET)(cStringIO.StringIO(data)) 

  def __iter__(self):
    return self

  def next(self):
    return self.reader.next().encode("utf-8")


class UnicodeReader:
  """
  A CSV reader which will iterate over lines in the CSV data buffer.
  The reader will scan the BOM header in the data to detect the right encoding. 
  """
  def __init__(self, data, **kwds):
    self.reader = csv.reader(UTF8Recoder(data), **kwds)

  def next(self):
    row = self.reader.next()
    return [unicode(s, "utf-8") for s in row]

  def __iter__(self):
    return self

    
class GridReport(View):
  '''
  The base class for all jqgrid views.
  The parameter values defined here are used as defaults for all reports, but
  can be overwritten.
  '''
  # Points to template to be used
  template = 'admin/base_site_grid.html'
  
  # The title of the report. Used for the window title
  title = ''

  # The resultset that returns a list of entities that are to be
  # included in the report.
  # This query is used to return the number of records.
  # It is also used to generate the actual results, in case no method
  # "query" is provided on the class.
  basequeryset = None

  # Specifies which column is used for an initial filter
  default_sort = (0, 'asc')
  
  # A model class from which we can inherit information.
  model = None

  # Allow editing in this report or not
  editable = True
  
  # Allow filtering of the results or not
  filterable = True
  
  # Include time bucket support in the report
  hasTimeBuckets = False
  
  # Show a select box in front to allow selection of records
  multiselect = True
  
  # Number of columns frozen in the report
  frozenColumns = 0

  # A list with required user permissions to view the report
  permissions = []
  
  # Extra variables added to the report template
  @classmethod
  def extra_context(reportclass, request, *args, **kwargs):
    return {}
  
  @method_decorator(staff_member_required)
  @method_decorator(csrf_protect)
  def dispatch(self, request, *args, **kwargs):    
    # Verify the user is authorized to view the report
    for perm in self.permissions:
      if not request.user.has_perm(perm):
        return HttpResponseForbidden('<h1>%s</h1>' % _('Permission denied'))
    
    # Dispatch to the correct method
    method = request.method.lower()
    if method == 'get':
      return self.get(request, *args, **kwargs)
    elif method == 'post':
      return self.post(request, *args, **kwargs)
    else:
      return HttpResponseNotAllowed(['get','post'])


  @classmethod
  def _generate_csv_data(reportclass, request, *args, **kwargs):
    sf = cStringIO.StringIO()    
    if get_format('DECIMAL_SEPARATOR', request.LANGUAGE_CODE, True) == ',':
      writer = csv.writer(sf, quoting=csv.QUOTE_NONNUMERIC, delimiter=';')
    else:
      writer = csv.writer(sf, quoting=csv.QUOTE_NONNUMERIC, delimiter=',')
    if translation.get_language() != request.LANGUAGE_CODE:
      translation.activate(request.LANGUAGE_CODE)

    # Write a Unicode Byte Order Mark header, aka BOM (Excel needs it to open UTF-8 file properly)
    encoding = settings.CSV_CHARSET
    sf.write(getBOM(encoding))
      
    # Write a header row
    fields = [ force_unicode(f.title).title().encode(encoding,"ignore") for f in reportclass.rows if f.title ]
    writer.writerow(fields)
    yield sf.getvalue()

    # Write the report content
    if callable(reportclass.basequeryset):
      query = reportclass._apply_sort(request, reportclass.filter_items(request, reportclass.basequeryset(request, args, kwargs), False).using(request.database))
    else:
      query = reportclass._apply_sort(request, reportclass.filter_items(request, reportclass.basequeryset).using(request.database))
        
    fields = [ i.field_name for i in reportclass.rows if i.field_name ]
    for row in hasattr(reportclass,'query') and reportclass.query(request,query) or query.values(*fields):
      # Clear the return string buffer
      sf.truncate(0)
      # Build the return value, encoding all output
      if hasattr(row, "__getitem__"):
        fields = [ row[f.field_name]==None and ' ' or unicode(_localize(row[f.field_name])).encode(encoding,"ignore") for f in reportclass.rows if f.name ]
      else:
        fields = [ getattr(row,f.field_name)==None and ' ' or unicode(_localize(getattr(row,f.field_name))).encode(encoding,"ignore") for f in reportclass.rows if f.name ]
      # Return string
      writer.writerow(fields)
      yield sf.getvalue()


  @classmethod
  def _apply_sort(reportclass, request, query):
    '''
    Applies a sort to the query. 
    '''
    sort = None
    if 'sidx' in request.GET: sort = request.GET['sidx']
    if not sort:
      if reportclass.default_sort:      
        sort = reportclass.rows[reportclass.default_sort[0]].name
      else:
        return query # No sorting 
    if ('sord' in request.GET and request.GET['sord'] == 'desc') or reportclass.default_sort[1] == 'desc':
      sort = "-%s" % sort  
    return query.order_by(sort)


  @classmethod
  def get_sort(reportclass, request):
    try: 
      if 'sidx' in request.GET:
        sort = 1
        ok = False
        for r in reportclass.rows:
          if r.name == request.GET['sidx']:
            ok = True 
            break
          sort += 1
        if not ok: sort = reportclass.default_sort[0] 
      else: 
        sort = reportclass.default_sort[0]      
    except: 
      sort = reportclass.default_sort[0]
    if ('sord' in request.GET and request.GET['sord'] == 'desc') or reportclass.default_sort[1] == 'desc':
      return "%s asc" % sort
    else:
      return "%s desc" % sort  


  @classmethod
  def _generate_json_data(reportclass, request, *args, **kwargs):
    page = 'page' in request.GET and int(request.GET['page']) or 1
    if callable(reportclass.basequeryset):
      query = reportclass.filter_items(request, reportclass.basequeryset(request, args, kwargs), False).using(request.database)
    else:
      query = reportclass.filter_items(request, reportclass.basequeryset).using(request.database)
    recs = query.count()
    total_pages = math.ceil(float(recs) / request.pagesize)
    if page > total_pages: page = total_pages
    if page < 1: page = 1
    query = reportclass._apply_sort(request, query)

    yield '{"total":%d,\n' % total_pages
    yield '"page":%d,\n' % page
    yield '"records":%d,\n' % recs
    yield '"rows":[\n'
    cnt = (page-1)*request.pagesize+1
    first = True

    # # TREEGRID
    #from django.db import connections, DEFAULT_DB_ALIAS
    #cursor = connections[DEFAULT_DB_ALIAS].cursor()
    #cursor.execute('''
    #  select node.name,node.description,node.category,node.subcategory,node.operation_id,node.owner_id,node.price,node.lastmodified,node.level,node.lft,node.rght,node.rght=node.lft+1
    #  from item as node
    #  left outer join item as parent0
    #    on node.lft between parent0.lft and parent0.rght and parent0.level = 0 and node.level >= 0
    #  left outer join item as parent1
    #    on node.lft between parent1.lft and parent1.rght and parent1.level = 1 and node.level >= 1
    #  left outer join item as parent2
    #    on node.lft between parent2.lft and parent2.rght and parent2.level = 2 and node.level >= 2
    #  where node.level = 0
    #  order by parent0.description asc, parent1.description asc, parent2.description asc, node.level, node.description, node.name
    #  ''')
    #for row in cursor.fetchall():
    #  if first:
    #    first = False
    #    yield '{"%s","%s","%s","%s","%s","%s","%s","%s",%d,%d,%d,%s,false]}\n' %(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10], row[11] and 'true' or 'false')
    #  else:
    #    yield ',{"%s","%s","%s","%s","%s","%s","%s","%s",%d,%d,%d,%s,false]}\n' %(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10], row[11] and 'true' or 'false')
    #yield ']}\n'

    # GridReport
    fields = [ i.field_name for i in reportclass.rows if i.field_name ]
    #if False: # TREEGRID
    #  fields.append('level')
    #  fields.append('lft')
    #  fields.append('rght')
    #  fields.append('isLeaf')
    #  fields.append('expanded')
    for i in hasattr(reportclass,'query') and reportclass.query(request,query) or query[cnt-1:cnt+request.pagesize].values(*fields):
      if first:
        r = [ '{' ]
        first = False
      else:
        r = [ ',\n{' ]
      first2 = True
      for f in reportclass.rows:
        if not f.name: continue
        if isinstance(i[f.field_name], basestring):
          s = '"%s"' % escape(i[f.field_name].encode(settings.DEFAULT_CHARSET,"ignore"))
        elif isinstance(i[f.field_name], (list,tuple)): 
          s = json.dumps(i[f.field_name], encoding = settings.DEFAULT_CHARSET)
        else:
          s = '"%s"' % i[f.field_name]
        if first2:
          # if isinstance(i[f.field_name], (list,tuple)): pegging report has a tuple of strings...
          r.append('"%s":%s' % (f.name,s))
          first2 = False
        elif i[f.field_name] != None:
          r.append(', "%s":%s' % (f.name,s))
      #if False:    # TREEGRID
      #  r.append(', %d, %d, %d, %s, %s' % (i['level'],i['lft'],i['rght'], i['isLeaf'] and 'true' or 'false', i['expanded'] and 'true' or 'false' ))
      r.append('}')
      yield ''.join(r)
    yield '\n]}\n'


  @classmethod
  def post(reportclass, request, *args, **kwargs):
    if "csv_file" in request.FILES:
      # Uploading a CSV file
      return reportclass.parseCSVupload(request)
    else:
      # Saving after inline edits
      return reportclass.parseJSONupload(request)


  @classmethod
  def get(reportclass, request, *args, **kwargs):
    fmt = request.GET.get('format', None)
    if not fmt:
      # Return HTML page
      # Pick up the list of time buckets      
      if reportclass.hasTimeBuckets:
        pref = request.user.get_profile()
        (bucket,start,end,bucketlist) = getBuckets(request, pref)
        bucketnames = Bucket.objects.order_by('name').values_list('name', flat=True)
      else:
        bucket = start = end = bucketlist = bucketnames = None
      context = {
        'reportclass': reportclass,
        'title': (args and args[0] and _('%(title)s for %(entity)s') % {'title': force_unicode(reportclass.title), 'entity':force_unicode(args[0])}) or reportclass.title,
        'object_id': args and args[0] or None,
        'reportbucket': bucket,
        'reportstart': start,
        'reportend': end,
        'is_popup': request.GET.has_key('pop'),
        'args': args,
        'filters': reportclass.getQueryString(request),
        'bucketnames': bucketnames,
        'bucketlist': bucketlist,
        'model': reportclass.model,
        'hasaddperm': reportclass.editable and reportclass.model and request.user.has_perm('%s.%s' % (reportclass.model._meta.app_label, reportclass.model._meta.get_add_permission())),
        'hasdeleteperm': reportclass.editable and reportclass.model and request.user.has_perm('%s.%s' % (reportclass.model._meta.app_label, reportclass.model._meta.get_delete_permission())),
        'haschangeperm': reportclass.editable and reportclass.model and request.user.has_perm('%s.%s' % (reportclass.model._meta.app_label, reportclass.model._meta.get_change_permission())),
        'active_tab': 'plan',
        }  
      for k, v in reportclass.extra_context(request, *args, **kwargs).iteritems():
        context[k] = v  
      return render(request, reportclass.template, context)
    elif fmt == 'json':
      # Return JSON data to fill the grid.
      # Response is not returned as an iterator to assure that the database 
      # connection is properly closed.
      return HttpResponse(
         mimetype = 'application/json; charset=%s' % settings.DEFAULT_CHARSET,
         content = ''.join(reportclass._generate_json_data(request, *args, **kwargs))
         )
    elif fmt == 'csvlist' or fmt == 'csvtable':
      # Return CSV data to export the data
      # Response is not returned as an iterator to assure that the database 
      # connection is properly closed.
      response = HttpResponse(
         mimetype= 'text/csv; charset=%s' % settings.CSV_CHARSET,
         content = ''.join(reportclass._generate_csv_data(request, *args, **kwargs))
         )
      response['Content-Disposition'] = 'attachment; filename=%s.csv' % iri_to_uri(reportclass.title.lower())
      return response
    else:
      raise Http404('Unknown format type')
  
  
  @classmethod
  def parseJSONupload(reportclass, request):
    # Check permissions
    if not reportclass.model or not reportclass.editable:
      return HttpResponseForbidden(_('Permission denied'))
    if not request.user.has_perm('%s.%s' % (reportclass.model._meta.app_label, reportclass.model._meta.get_change_permission())):
      return HttpResponseForbidden(_('Permission denied'))
  
    # Loop over the data records 
    transaction.enter_transaction_management(using=request.database)
    transaction.managed(True, using=request.database)
    resp = HttpResponse()
    ok = True
    try:          
      content_type_id = ContentType.objects.get_for_model(reportclass.model).pk      
      for rec in simplejson.JSONDecoder().decode(request.read()):     
        if 'delete' in rec:
          # Deleting records
          for key in rec['delete']:
            try: 
              obj = reportclass.model.objects.using(request.database).get(pk=key)
              obj.delete()
              LogEntry(
                  user_id         = request.user.id,
                  content_type_id = content_type_id,
                  object_id       = force_unicode(key),
                  object_repr     = force_unicode(key)[:200],
                  action_flag     = DELETION
              ).save(using=request.database)       
            except reportclass.model.DoesNotExist:
              ok = False
              resp.write(escape(_("Can't find %s" % key))) 
              resp.write('<br/>')
              pass
            except Exception as e:
              ok = False
              resp.write(escape(e))
              resp.write('<br/>')
              pass
        elif 'copy' in rec:
          # Copying records
          for key in rec['copy']:
            try: 
              obj = reportclass.model.objects.using(request.database).get(pk=key)
              if isinstance(reportclass.model._meta.pk, CharField):
                # The primary key is a string
                obj.pk = "Copy of %s" % key
              elif isinstance(reportclass.model._meta.pk, AutoField):
                # The primary key is an auto-generated number
                obj.pk = None
              else:
                raise Exception(_("Can't copy %s") % reportclass.model._meta.app_label)
              obj.save(using=request.database, force_insert=True)
              LogEntry(
                  user_id         = request.user.pk,
                  content_type_id = content_type_id,
                  object_id       = obj.pk,
                  object_repr     = force_unicode(obj),
                  action_flag     = ADDITION,
                  change_message  = _('Copied from %s.') % key
              ).save(using=request.database)   
              transaction.commit(using=request.database)            
            except reportclass.model.DoesNotExist:
              ok = False
              resp.write(escape(_("Can't find %s" % key))) 
              resp.write('<br/>')
              transaction.rollback(using=request.database)
              pass
            except Exception as e:
              ok = False
              resp.write(escape(e))
              resp.write('<br/>')
              transaction.rollback(using=request.database)
              pass        
        else:   
          # Editing records
          try:
            obj = reportclass.model.objects.using(request.database).get(pk=rec['id'])
            del rec['id']
            UploadForm = modelform_factory(reportclass.model,
              fields = tuple(rec.keys()),
              formfield_callback = lambda f: (isinstance(f, RelatedField) and f.formfield(using=request.database)) or f.formfield()
              )
            form = UploadForm(rec, instance=obj)
            if form.has_changed():
              obj = form.save()
              LogEntry(
                  user_id         = request.user.pk,
                  content_type_id = content_type_id,
                  object_id       = obj.pk,
                  object_repr     = force_unicode(obj),
                  action_flag     = CHANGE,
                  change_message  = _('Changed %s.') % get_text_list(form.changed_data, _('and'))
              ).save(using=request.database)
          except reportclass.model.DoesNotExist:
            ok = False
            resp.write(escape(_("Can't find %s" % obj.pk))) 
            resp.write('<br/>')                          
          except Exception as e: 
            ok = False
            for error in form.non_field_errors():
              resp.write(escape('%s: %s' % (obj.pk, error)))            
              resp.write('<br/>')                          
            for field in form:
              for error in field.errors:
                resp.write(escape('%s %s: %s: %s' % (obj.pk, field.name, rec[field.name], error)))                        
                resp.write('<br/>')                          
    finally:
      transaction.commit(using=request.database)
      transaction.leave_transaction_management(using=request.database)
    if ok: resp.write("OK")
    resp.status_code = ok and 200 or 403
    return resp
  
      
  @classmethod
  def parseCSVupload(reportclass, request):
      '''
      This method reads CSV data from a string (in memory) and creates or updates
      the database records.
      The data must follow the following format:
        - the first row contains a header, listing all field names
        - a first character # marks a comment line
        - empty rows are skipped
      '''      
      # Check permissions
      if not reportclass.model:
        messages.add_message(request, messages.ERROR, _('Invalid upload request'))
        return HttpResponseRedirect(request.prefix + request.get_full_path())
      if not reportclass.editable or not request.user.has_perm('%s.%s' % (reportclass.model._meta.app_label, reportclass.model._meta.get_add_permission())):
        messages.add_message(request, messages.ERROR, _('Permission denied'))
        return HttpResponseRedirect(request.prefix + request.get_full_path())

      # Choose the right delimiter and language
      delimiter= get_format('DECIMAL_SEPARATOR', request.LANGUAGE_CODE, True) == ',' and ';' or ','
      if translation.get_language() != request.LANGUAGE_CODE:
        translation.activate(request.LANGUAGE_CODE)

      # Init
      headers = []
      rownumber = 0
      changed = 0
      added = 0
      warnings = []
      errors = []
      content_type_id = ContentType.objects.get_for_model(reportclass.model).pk
            
      transaction.enter_transaction_management(using=request.database)
      transaction.managed(True, using=request.database)
      try:
        # Loop through the data records
        has_pk_field = False
        for row in UnicodeReader(request.FILES['csv_file'].read(), delimiter=delimiter):
          rownumber += 1
  
          ### Case 1: The first line is read as a header line
          if rownumber == 1:
            for col in row:
              col = col.strip().strip('#').lower()
              if col == "":
                headers.append(False)
                continue
              ok = False
              for i in reportclass.model._meta.fields:
                if col == i.name.lower() or col == i.verbose_name.lower():
                  if i.editable == True:
                    headers.append(i)
                  else:
                    headers.append(False)
                  ok = True
                  break
              if not ok: errors.append(_('Incorrect field %(column)s') % {'column': col})
              if col == reportclass.model._meta.pk.name.lower() \
                or col == reportclass.model._meta.pk.verbose_name.lower():
                  has_pk_field = True
            if not has_pk_field and not isinstance(reportclass.model._meta.pk, AutoField):
              # The primary key is not an auto-generated id and it is not mapped in the input...
              errors.append(_('Missing primary key field %(key)s') % {'key': reportclass.model._meta.pk.name})
            # Abort when there are errors
            if len(errors) > 0: break   

            # Create a form class that will be used to validate the data
            UploadForm = modelform_factory(reportclass.model,
              fields = tuple([i.name for i in headers if isinstance(i,Field)]),
              formfield_callback = lambda f: (isinstance(f, RelatedField) and f.formfield(using=request.database, localize=True)) or f.formfield(localize=True)
              )
  
          ### Case 2: Skip empty rows and comments rows
          elif len(row) == 0 or row[0].startswith('#'):
            continue
  
          ### Case 3: Process a data row
          else:
            try:
              # Step 1: Build a dictionary with all data fields
              d = {}
              colnum = 0
              for col in row:
                # More fields in data row than headers. Move on to the next row.
                if colnum >= len(headers): break
                if isinstance(headers[colnum],Field): d[headers[colnum].name] = col.strip()
                colnum += 1
  
              # Step 2: Fill the form with data, either updating an existing
              # instance or creating a new one.
              if has_pk_field:
                # A primary key is part of the input fields
                try:
                  # Try to find an existing record with the same primary key
                  it = reportclass.model.objects.using(request.database).get(pk=d[reportclass.model._meta.pk.name])
                  form = UploadForm(d, instance=it)
                except reportclass.model.DoesNotExist:                  
                  form = UploadForm(d)
                  it = None
              else:
                # No primary key required for this model                
                form = UploadForm(d)
                it = None
  
              # Step 3: Validate the data and save to the database
              if form.has_changed():
                try:
                  obj = form.save()
                  LogEntry(
                      user_id         = request.user.pk,
                      content_type_id = content_type_id,
                      object_id       = obj.pk,
                      object_repr     = force_unicode(obj),
                      action_flag     = it and CHANGE or ADDITION,
                      change_message  = _('Changed %s.') % get_text_list(form.changed_data, _('and'))
                  ).save(using=request.database)
                  if it:
                    changed += 1
                  else:
                    added += 1
                except Exception as e:
                  # Validation fails
                  for error in form.non_field_errors():
                    warnings.append(
                      _('Row %(rownum)s: %(message)s') % {
                        'rownum': rownumber, 'message': error
                      })
                  for field in form:
                    for error in field.errors:
                      warnings.append(
                        _('Row %(rownum)s field %(field)s: %(data)s: %(message)s') % {
                          'rownum': rownumber, 'data': d[field.name],
                          'field': field.name, 'message': error
                        })
  
              # Step 4: Commit the database changes from time to time
              if rownumber % 500 == 0: transaction.commit(using=request.database)
            except Exception as e:
              errors.append(_("Exception during upload: %(message)s") % {'message': e,})
      finally:
        transaction.commit(using=request.database)
        transaction.leave_transaction_management(using=request.database)
  
      # Report all failed records
      if len(errors) > 0:
        messages.add_message(request, messages.INFO,
         _('File upload aborted with errors: changed %(changed)d and added %(added)d records') % {'changed': changed, 'added': added}
         )
        for i in errors: messages.add_message(request, messages.INFO, i)
      elif len(warnings) > 0:
        messages.add_message(request, messages.INFO,
          _('Uploaded file processed with warnings: changed %(changed)d and added %(added)d records') % {'changed': changed, 'added': added}
          )
        for i in warnings: messages.add_message(request, messages.INFO, i)
      else:
        messages.add_message(request, messages.INFO,
          _('Uploaded data successfully: changed %(changed)d and added %(added)d records') % {'changed': changed, 'added': added}
          )
      return HttpResponseRedirect(request.prefix + request.get_full_path())   


  @classmethod
  def _getRowByName(reportclass, name):
    if not hasattr(reportclass,'_rowsByName'):
      reportclass._rowsByName = {}
      for i in reportclass.rows:
        reportclass._rowsByName[i.name] = i
        if i.field_name != i.name:
          reportclass._rowsByName[i.field_name] = i
    return reportclass._rowsByName[name]

  
  _filter_map_jqgrid_django = {
      # jqgrid op: (django_lookup, use_exclude)
      'ne': ('%(field)s__exact', True),
      'bn': ('%(field)s__startswith', True),
      'en': ('%(field)s__endswith',  True),
      'nc': ('%(field)s__contains', True),
      'ni': ('%(field)s__in', True),
      'in': ('%(field)s__in', False),
      'eq': ('%(field)s__exact', False),
      'bw': ('%(field)s__startswith', False),
      'gt': ('%(field)s__gt', False),
      'ge': ('%(field)s__gte', False),
      'lt': ('%(field)s__lt', False),
      'le': ('%(field)s__lte', False),
      'ew': ('%(field)s__endswith', False),
      'cn': ('%(field)s__contains', False)
  }
  

  _filter_map_django_jqgrid = {
      # django lookup: jqgrid op
      'in': 'in',
      'exact': 'eq',
      'startswith': 'bw',
      'gt': 'gt',
      'gte': 'ge',
      'lt': 'lt',
      'lte': 'le',
      'endswith': 'ew',
      'contains': 'cn',
  }
      
      
  @classmethod
  def getQueryString(reportclass, request):
    # Django-style filtering (which uses URL parameters) are converted to a jqgrid filter expression
    filtered = False
    filters = ['{"groupOp":"AND","rules":[']
    for i,j in request.GET.iteritems():
      for r in reportclass.rows:
        if r.field_name and i.startswith(r.field_name):
          operator = (i==r.field_name) and 'exact' or i[i.rfind('_')+1:]
          try: 
            filters.append('{"field":"%s","op":"%s","data":"%s"},' % (r.field_name, reportclass._filter_map_django_jqgrid[operator], j))
            filtered = True
          except: pass # Ignore invalid operators
    if not filtered: return None
    filters.append(']}')
    return ''.join(filters)
        
                
  @classmethod
  def _get_q_filter(reportclass, filterdata):
    q_filters = []
    for rule in filterdata['rules']:
        try: 
          op, field, data = rule['op'], rule['field'], rule['data']
          filter_fmt, exclude = reportclass._filter_map_jqgrid_django[op]
          filter_str = smart_str(filter_fmt % {'field': reportclass._getRowByName(field).field_name})
          if filter_fmt.endswith('__in'):
              filter_kwargs = {filter_str: data.split(',')}
          else:
              filter_kwargs = {filter_str: smart_str(data)}
          if exclude:
              q_filters.append(~models.Q(**filter_kwargs))
          else:
              q_filters.append(models.Q(**filter_kwargs))
        except:
          pass # Silently ignore invalid filters    
    if u'groups' in filterdata:
      for group in filterdata['groups']:
        try:
          z = reportclass._get_q_filter(group)
          if z: q_filters.append(z)
        except:
          pass # Silently ignore invalid groups
    if len(q_filters) == 0:
      return None
    elif filterdata['groupOp'].upper() == 'OR':
      return reduce(operator.ior, q_filters)
    else:
      return reduce(operator.iand, q_filters)

      
  @classmethod
  def filter_items(reportclass, request, items, plus_django_style=True):

    filters = None

    # Jqgrid-style filtering
    if request.GET.get('_search') == 'true':     
      # Validate complex search JSON data
      _filters = request.GET.get('filters')
      try:
        filters = _filters and json.loads(_filters)
      except ValueError:
        filters = None
  
      # Single field searching, which is currently not used
      if filters is None:
        field = request.GET.get('searchField')
        op = request.GET.get('searchOper')
        data = request.GET.get('searchString')
        if all([field, op, data]):
          filters = {
              'groupOp': 'AND',
              'rules': [{ 'op': op, 'field': field, 'data': data }]
          }    
    if filters:
      z = reportclass._get_q_filter(filters)
      if z: 
        return items.filter(z)
      else: 
        return items
    
    # Django-style filtering, using URL parameters
    if plus_django_style:
      for i,j in request.GET.iteritems():
        for r in reportclass.rows:
          if r.name and i.startswith(r.field_name):
            try: items = items.filter(**{i:j})
            except: pass # silently ignore invalid filters
    return items

  
class GridPivot(GridReport):

  # Cross definitions.
  # Possible attributes for a cross field are:
  #   - title:
  #     Name of the cross that is displayed to the user.
  #     It defaults to the name of the field.
  #   - editable:
  #     True when the field is editable in the page.
  #     The default value is false.
  crosses = ()

  template = 'admin/base_site_gridpivot.html'
  
  hasTimeBuckets = True  

  editable = False

  multiselect = False
  
  @classmethod
  def _apply_sort(reportclass, request):
    '''
    Returns the index of the column to sort on. 
    '''
    sort = 'sidx' in request.GET and request.GET['sidx'] or reportclass.rows[0].name
    idx = 1
    for i in reportclass.rows:
      if i.name == sort: 
        if 'sord' in request.GET and request.GET['sord'] == 'desc':
          return idx > 1 and "%d desc, 1 asc" % idx or "1 desc"
        else:
          return idx > 1 and "%d asc, 1 asc" % idx or "1 asc"
      else:
        idx += 1 
    return "1 asc"

  
  @classmethod
  def _generate_json_data(reportclass, request, *args, **kwargs):

    # Pick up the list of time buckets      
    pref = request.user.get_profile()
    (bucket,start,end,bucketlist) = getBuckets(request, pref)

    # Prepare the query   
    if args and args[0]:
      page = 1
      recs = 1
      total_pages = 1
      query = reportclass.query(request, reportclass.basequeryset.filter(pk__exact=args[0]).using(request.database), bucket, start, end, sortsql="1 asc")
    else:
      page = 'page' in request.GET and int(request.GET['page']) or 1
      if callable(reportclass.basequeryset):
        recs = reportclass.filter_items(request, reportclass.basequeryset(request, args, kwargs), False).using(request.database).count()
      else:
        recs = reportclass.filter_items(request, reportclass.basequeryset).using(request.database).count()
      total_pages = math.ceil(float(recs) / request.pagesize)
      if page > total_pages: page = total_pages
      if page < 1: page = 1
      cnt = (page-1)*request.pagesize+1
      if callable(reportclass.basequeryset):
        query = reportclass.query(request, reportclass.filter_items(request, reportclass.basequeryset(request, args, kwargs), False).using(request.database)[cnt-1:cnt+request.pagesize], bucket, start, end, sortsql=reportclass._apply_sort(request))
      else:
        query = reportclass.query(request, reportclass.filter_items(request, reportclass.basequeryset).using(request.database)[cnt-1:cnt+request.pagesize], bucket, start, end, sortsql=reportclass._apply_sort(request))

    # Generate header of the output
    yield '{"total":%d,\n' % total_pages
    yield '"page":%d,\n' % page
    yield '"records":%d,\n' % recs
    yield '"rows":[\n'
    
    # Generate output
    currentkey = None
    r = []
    for i in query:
      # We use the first field in the output to recognize new rows.
      if currentkey <> i[reportclass.rows[0].name]:
        # New line
        if currentkey:
          yield ''.join(r)
          r = [ '},\n{' ]
        else:
          r = [ '{' ] 
        currentkey = i[reportclass.rows[0].name]
        first2 = True
        for f in reportclass.rows:   
          try:       
            s = isinstance(i[f.name], basestring) and escape(i[f.name].encode(settings.DEFAULT_CHARSET,"ignore")) or i[f.name]
            if first2:
              r.append('"%s":"%s"' % (f.name,s))
              first2 = False
            elif i[f.name] != None:
              r.append(', "%s":"%s"' % (f.name,s))
          except: pass
      r.append(', "%s":[' % i['bucket'])
      first2 = True
      for f in reportclass.crosses:
        if first2:
          r.append('%s' % i[f[0]])
          first2 = False
        else:
          r.append(', %s' % i[f[0]])
      r.append(']')
    r.append('}')
    r.append('\n]}\n')
    yield ''.join(r)


  @classmethod
  def _generate_csv_data(reportclass, request, *args, **kwargs):
    sf = cStringIO.StringIO()    
    if get_format('DECIMAL_SEPARATOR', request.LANGUAGE_CODE, True) == ',':
      writer = csv.writer(sf, quoting=csv.QUOTE_NONNUMERIC, delimiter=';')
    else:
      writer = csv.writer(sf, quoting=csv.QUOTE_NONNUMERIC, delimiter=',')
    if translation.get_language() != request.LANGUAGE_CODE:
      translation.activate(request.LANGUAGE_CODE)
    listformat = (request.GET.get('format','csvlist') == 'csvlist')
      
    # Pick up the list of time buckets      
    pref = request.user.get_profile()
    (bucket,start,end,bucketlist) = getBuckets(request, pref)

    # Prepare the query
    if args and args[0]:
      query = reportclass.query(request, reportclass.basequeryset.filter(pk__exact=args[0]).using(request.database), bucket, start, end, sortsql="1 asc")
    elif callable(reportclass.basequeryset):
      query = reportclass.query(request, reportclass.filter_items(request, reportclass.basequeryset(request, args, kwargs), False).using(request.database), bucket, start, end, sortsql=reportclass._apply_sort(request))
    else:
      query = reportclass.query(request, reportclass.filter_items(request, reportclass.basequeryset).using(request.database), bucket, start, end, sortsql=reportclass._apply_sort(request))

    # Write a Unicode Byte Order Mark header, aka BOM (Excel needs it to open UTF-8 file properly)
    encoding = settings.CSV_CHARSET
    sf.write(getBOM(encoding))

    # Write a header row
    fields = [ force_unicode(f.title).title().encode(encoding,"ignore") for f in reportclass.rows if f.name ]
    if listformat:
      fields.extend([ capfirst(force_unicode(_('bucket'))).encode(encoding,"ignore") ])
      fields.extend([ ('title' in s[1] and capfirst(_(s[1]['title'])) or capfirst(_(s[0]))).encode(encoding,"ignore") for s in reportclass.crosses ])
    else:
      fields.extend( [capfirst(_('data field')).encode(encoding,"ignore")])
      fields.extend([ unicode(b['name']).encode(encoding,"ignore") for b in bucketlist])
    writer.writerow(fields)
    yield sf.getvalue()

    # Write the report content
    if listformat:
      for row in query:
        # Clear the return string buffer
        sf.truncate(0)
        # Data for rows
        if hasattr(row, "__getitem__"):
          fields = [ row[f.name]==None and ' ' or unicode(row[f.name]).encode(encoding,"ignore") for f in reportclass.rows if f.name ]
          fields.extend([ row['bucket'].encode(encoding,"ignore") ])
          fields.extend([ row[f[0]]==None and ' ' or unicode(_localize(row[f[0]])).encode(encoding,"ignore") for f in reportclass.crosses ])
        else:
          fields = [ getattr(row,f.name)==None and ' ' or unicode(getattr(row,f.name)).encode(encoding,"ignore") for f in reportclass.rows if f.name ]
          fields.extend([ getattr(row,'bucket').encode(encoding,"ignore") ])
          fields.extend([ getattr(row,f[0])==None and ' ' or unicode(_localize(getattr(row,f[0]))).encode(encoding,"ignore") for f in reportclass.crosses ])
        # Return string
        writer.writerow(fields)
        yield sf.getvalue()
    else:
      currentkey = None
      for row in query:
        # We use the first field in the output to recognize new rows.
        if not currentkey:
          currentkey = row[reportclass.rows[0].name]
          row_of_buckets = [ row ]
        elif currentkey == row[reportclass.rows[0].name]:
          row_of_buckets.append(row)
        else:
          # Write an entity
          for cross in reportclass.crosses:
            # Clear the return string buffer
            sf.truncate(0)
            fields = [ unicode(row_of_buckets[0][s.name]).encode(encoding,"ignore") for s in reportclass.rows if s.name ]
            fields.extend( [('title' in cross[1] and capfirst(_(cross[1]['title']))).encode(encoding,"ignore") or capfirst(_(cross[0])).encode(encoding,"ignore")] )
            fields.extend([ unicode(_localize(bucket[cross[0]])).encode(encoding,"ignore") for bucket in row_of_buckets ])
            # Return string
            writer.writerow(fields)
            yield sf.getvalue()
          currentkey = row[reportclass.rows[0].name]
          row_of_buckets = [row]
      # Write the last entity
      for cross in reportclass.crosses:
        # Clear the return string buffer
        sf.truncate(0)
        fields = [ unicode(row_of_buckets[0][s.name]).encode(encoding,"ignore") for s in reportclass.rows if s.name ]
        fields.extend( [('title' in cross[1] and capfirst(_(cross[1]['title']))).encode(encoding,"ignore") or capfirst(_(cross[0])).encode(encoding,"ignore")] )
        fields.extend([ unicode(_localize(bucket[cross[0]])).encode(encoding,"ignore") for bucket in row_of_buckets ])
        # Return string
        writer.writerow(fields)
        yield sf.getvalue()
            

def _localize(value, use_l10n=None):
  '''
  Localize numbers.
  Dates are always represented as YYYY-MM-DD hh:mm:ss since this is
  a format that is understood uniformly across different regions in the
  world.
  '''
  if callable(value):
    value = value()
  if isinstance(value, (Decimal, float, int, long)):
    return number_format(value, use_l10n=use_l10n)
  elif isinstance(value, (list,tuple) ):
    return "|".join([ unicode(_localize(i)) for i in value ])
  else:
    return value


def getBuckets(request, pref=None, bucket=None, start=None, end=None):
  '''
  This function gets passed a name of a bucketization.
  It returns a list of buckets.
  The data are retrieved from the database table dates, and are
  stored in a python variable for performance
  '''
  # Pick up the user preferences
  if pref == None: pref = request.user.get_profile()

  # Select the bucket size (unless it is passed as argument)
  if not bucket:
    bucket = request.GET.get('reportbucket')
    if not bucket:
      try:
        bucket = Bucket.objects.using(request.database).get(name=pref.buckets)
      except:
        try: bucket = Bucket.objects.using(request.database).order_by('name')[0].name
        except: bucket = None
    elif pref.buckets != bucket:
      try: pref.buckets = Bucket.objects.using(request.database).get(name=bucket).name
      except: bucket = None
      pref.save()

  # Select the start date (unless it is passed as argument)
  if not start:
    start = request.GET.get('reportstart')
    if start:
      try:
        (y,m,d) = start.split('-')
        start = datetime(int(y),int(m),int(d))
        if pref.startdate != start:
          pref.startdate = start
          pref.save()
      except:
        try: start = pref.startdate
        except: pass
        if not start:
          try: start = datetime.strptime(Parameter.objects.get(name="currentdate").value, "%Y-%m-%d %H:%M:%S")
          except: start = datetime.now()
    else:
      try: start = pref.startdate
      except: pass
      if not start:
        try: start = datetime.strptime(Parameter.objects.get(name="currentdate").value, "%Y-%m-%d %H:%M:%S")
        except: start = datetime.now()

  # Select the end date (unless it is passed as argument)
  if not end:
    end = request.GET.get('reportend')
    if end:
      try:
        (y,m,d) = end.split('-')
        end = datetime(int(y),int(m),int(d))
        if pref.enddate != end:
          pref.enddate = end
          pref.save()
      except:
        try: end = pref.enddate
        except: pass
    else:
      try: end = pref.enddate
      except: pass

  # Filter based on the start and end date
  if not bucket:
    return (None, start, end, None)
  else:
    res = BucketDetail.objects.using(request.database).filter(bucket=bucket)
    if start: res = res.filter(enddate__gt=start)
    if end: res = res.filter(startdate__lte=end)
    return (unicode(bucket), start, end, res.values('name','startdate','enddate'))
