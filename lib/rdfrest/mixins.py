#    This file is part of RDF-REST <http://liris.cnrs.fr/sbt-dev/ktbs>
#    Copyright (C) 2011 Pierre-Antoine Champin <pchampin@liris.cnrs.fr> /
#    Universite de Lyon <http://www.universite-lyon.fr>
#
#    RDF-REST is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Lesser General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    RDF-REST is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public License
#    along with RDF-REST.  If not, see <http://www.gnu.org/licenses/>.

"""
I provide useful mixin classes to enhance `rdflib.resource.Resource`.
"""
from itertools import chain
from md5 import md5
from rdflib import BNode, Literal, Graph, RDF, URIRef
from rdflib.compare import graph_diff
from time import time

from rdfrest.exceptions import InvalidDataError, MethodNotAllowedError, \
    RdfRestException
from rdfrest.namespaces import RDFREST
from rdfrest.resource import Resource
from rdfrest.utils import cache_result

class RdfPutMixin(Resource):
    """I make the mixed-in class PUTable.

    I provide a simple implementation of
    :meth:`~rdfrest.resource.Resource.rdf_put`: no paramaters are accepted,
    and any graph is accepted as long as it passes 
    :meth:`~rdfrest.resource.Resource.check_new_graph`.
    """

    def rdf_put(self, new_graph, parameters=None):
        """I override :meth:`rdfrest.resource.Resource.rdf_put`
        """
        if parameters is not None:
            # an empty dict means that a '?' has been added to the URI
            # so it counts as having parameters
            raise MethodNotAllowedError("PUT on %s with parameters" % self.uri)
        errors = self.check_new_graph(self.uri, new_graph, self)
        if errors:
            raise InvalidDataError(errors)
        self._graph.remove((None, None, None))
        add = self._graph.add
        for triple in new_graph:
            add(triple)


class RdfPostMixin(Resource):
    """I make mixed-in class POSTable in order to create child resources.

    I provide a simple implementation of
    :meth:`~rdfrest.resource.Resource.rdf_post`: no paramaters are accepted;
    the created resource in the posted graph is recognized by being linked
    to this resource.

    This basic behaviour can be customized by overriding the following methods:

    * :meth:`find_created`
    * :meth:`check_posted_graph`
    * :meth:`ack_created`
    """

    def rdf_post(self, new_graph, parameters=None):
        """I override :meth:`rdfrest.resource.Resource.rdf_post`
        """
        if parameters is not None:
            # an empty dict means that a '?' has been added to the URI
            # so it counts as having parameters
            raise MethodNotAllowedError("POST on %s with parameters"
                                        % self.uri)
        created = self.find_created(new_graph)
        if created is None:
            raise RdfRestException("Can not find created node")
        errors = self.check_posted_graph(new_graph, created)
        if errors:
            raise InvalidDataError(errors)

        get_class = self.service._class_map.get #pylint: disable=W0212
        resource_class = None
        for typ in new_graph.objects(created, RDF.type):
            candidate = get_class(typ)
            if candidate is None:
                continue
            if resource_class:
                raise InvalidDataError("Ambiguous type of posted resource")
            resource_class = candidate

        if not isinstance(created, URIRef):
            new_uri = resource_class.mint_uri(self, new_graph, created)
            add_triple = new_graph.add
            rem_triple = new_graph.remove
            subst = lambda x: (x == created) and new_uri or x
            for triple in new_graph:
                # heuristics: most triple will involve created, so we replace
                # all of them, even those that will not be changed by subst
                rem_triple(triple)
                add_triple([ subst(i) for i in triple ])
            created = new_uri

        posted = resource_class.create(self.service, created, new_graph)
        self.ack_created(posted)
        return [created]
 
    def find_created(self, new_graph):
        """Find the node represented the resource to create in `new_graph`.

        :param new_graph: the posted RDF graph
        :type  new_graph: rflib.Graph

        :return: the node representing the resource to create, or None if it
                 can not be found.
        :rtype: rdflib.Node

        This implementation uses the following heuristics to guess the
        created node, but you may wish to override it:

        * if the target URI is linked to a single node, that node is returned
          (unless it is a litteral or a URI with a fragment)
        * if the targer URI is linked to several node, one of them being a URI
          without fragment, and all the others being URIRef of that URI,
          then the URI without fragment is returned
        """
        query_objects = "SELECT ?o WHERE { <%s> ?p ?o }" % self.uri
        query_subjects = "SELECT ?s WHERE { ?s ?p <%s> }" % self.uri
        candidates = set(chain(new_graph.query(query_objects),
                               new_graph.query(query_subjects)))
        candidates = [ cand for cand in candidates
                       if not isinstance(cand, Literal) ]

        if len(candidates) == 1:
            candidate = candidates[0]
            if isinstance(candidate, URIRef) and '#' in candidate:
                #print "===", "only candidate has a fragment %s" % candidate
                return None
            else:
                return candidate

        base_candidates = [ cand for cand in candidates
                            if isinstance(cand, URIRef)
                            and "#" not in cand ]
        if len(base_candidates) != 1:
            #print "===", "not just one candidate %s" % candidates
            return None

        base = base_candidates.pop()
        candidates.remove(base)
        prefix = base+"#"
        for other in candidates:
            if isinstance(other, BNode):
                #print "===", "bnodes + base"
                return None # can not decide between 'base' and blank nodes
            # else its is a URIRef (Literals have been removed before)
            if not other.startswith(prefix):
                #print "===", "other is not a fragment of base"
                return None
        return base # all other candidates are 'fragments' of the base

    def check_posted_graph(self, new_graph, created):
        """Check whether `new_graph` is acceptable for a POST on this resource.

        :param new_graph: the posted RDF graph
        :type  new_graph: rflib.Graph
        :param created:   the node representing the resource to create
        :type  created:   rdflib.Node

        :return: `None` on success, else an error message

        This implementation does nothing.
        """

    def ack_created(self, created_resource):
        """Performs 

        :param created_resource: the just-created resource

        This implementation does nothing.
        """

    @classmethod
    def check_new_graph(cls, uri, new_graph,
                        resource=None, added=None, removed=None):
        """I overrides :meth:`rdfrest.resource.Resource.check_new_graph`
        """
        ret = super(RdfPostMixin, cls).check_new_graph(uri, new_graph)
        if uri[-1] != "/":
            ret = _join_errors(ret, "URI must end with '/'")
        return ret

    @classmethod
    def mint_uri(cls, target, new_graph, created):
        """I overrides :meth:`rdfrest.resource.Resource.mint_uri`
        """
        uri = super(RdfPostMixin, cls).mint_uri(target, new_graph, created)
        if uri[-1] != "/":
            uri = URIRef(uri+"/")
        return uri


class BookkeepingMixin(Resource):
    """I add bookkeeping metadata to the mixed-in class.

    Bookkeeping metadata consist of:

    * a weak etag (as defined in section 13.3 of :RFC:`2616`)
    * a last-modified data

    .. warning::

        Bookkeeping metadata are only automatically managed when using
        :meth:`~rdfrest.resource.Resource.rdf_put` and
        :meth:`~rdfrest.resource.Resource.rdf_post` to update the underlying
        RDF graph.

        Whenever the graph is modified by other ways,
        :meth:`update_bk_metadata` should be invoked.

    .. note::
    
        We are using *weak* etags, because the tag applies to an
        abstract graph, not to a given serialization.
    """

    def update_bk_metadata(self):
        """Update the metadata (etag and last-modified) after a change.

        This method is automatically called by :meth:`rdf_put` and
        :meth:`rdf_post`.
        """
        now = time()
        new_etag = md5(str(now)).hexdigest()
        self._private.set((self.uri, _ETAG, Literal(new_etag)))
        self._private.set((self.uri, _LAST_MODIFIED, Literal(now)))

    @property
    def etag(self):
        """I return the etag of this resource.
        """
        return str(next(self._private.objects(self.uri, _ETAG)))

    @property
    def last_modified(self):
        """I return the time when this resource was last modified.

        :return: number of seconds since EPOCH, as returned by `time.time()`
        """
        return next(self._private.objects(self.uri, _LAST_MODIFIED)).toPython()

    @classmethod
    def store_new_graph(cls, service, uri, new_graph):
        """I override :meth:`rdfrest.resource.Resource.store_new_graph` to
        generate bookkeeping metadata.
        """
        super(BookkeepingMixin, cls).store_new_graph(service, uri, new_graph)
        now = time()
        new_etag = md5(str(now)).hexdigest()
        private_add = Graph(service.store, URIRef(uri+"#private")).add
        private_add((uri, _ETAG, Literal(new_etag)))
        private_add((uri, _LAST_MODIFIED, Literal(now)))

    def rdf_put(self, new_graph, parameters=None):
        """I override :meth:`rdfrest.resource.Resource.rdf_put` to
        automatically invoke :meth:`update_bk_metadata`.
        """
        super(BookkeepingMixin, self).rdf_put(new_graph, parameters)
        self.update_bk_metadata()

    def rdf_post(self, new_graph, parameters=None):
        """I override :meth:`rdfrest.resource.Resource.rdf_post` to
        automatically invoke :meth:`update_bk_metadata`.
        """
        ret = super(BookkeepingMixin, self).rdf_post(new_graph, parameters)
        self.update_bk_metadata()
        return ret


class WithReservedNamespacesMixin(Resource):
    """ 
    I add reserved namespaces to RdfPutMixin and RdfPostMixin.

    A *reserved namespace* is a set of URIs (defined by a common prefix) which
    can not be freely used in PUT or POST requests, as they have a specific
    meaning for the application.

    Reserved namespaces are listed in the `RDF_RESERVED_NS` class variable (in addition
    to those inherited from superclasses).

    The reserved namespace applies to URIs used as predicates and78 types.
    The default rule is that they can not be added at POST time (except for
    the *main type* of the resource), and they can not be changed at PUT time.
    They can only be inserted and modified by the service itself.

    It is however possible to provide exceptions for a class, *i.e.* URIs
    inside a reserved namespace which is usable in a given context: POST only,
    or PUT (and POST); incoming property, outgoing property or type. All those
    exceptions are listed in the corresponding class variable from the list
    below (in addition to those inherited from superclasses).

    * `RDF_POSTABLE_IN`
    * `RDF_POSTABLE_OUT`
    * `RDF_POSTABLE_TYPES`
    * `RDF_PUTABLE_IN`
    * `RDF_PUTABLE_OUT`
    * `RDF_PUTABLE_TYPES`

    NB: the values of the class variables are *not* supposed to change over
    time; if they do, the change may not be effective.
    """

    @classmethod
    def check_new_graph(cls, uri, new_graph,
                        resource=None, added=None, removed=None):
        """I overrides :meth:`rdfrest.resource.Resource.check_new_graph` to
        check the reserved namespace constraints.
        """
        if resource is not None:
            if added is None or removed is None:
                _, added, removed = graph_diff(
                    new_graph,
                    resource._graph # protected member #pylint: disable=W0212
                    )

        errors = super(WithReservedNamespacesMixin, cls) \
            .check_new_graph(uri, new_graph, resource, added, removed)

        if resource is None:
            new_errors = cls.__check_triples(new_graph, "POST", uri)
        else:
            triples = chain(removed, added)
            new_errors = cls.__check_triples(triples, "PUT", uri)

        return _join_errors(errors, new_errors)

    #
    # private method
    #

    @classmethod
    @cache_result
    def __get_reserved_namespaces(cls):
        """
        Cache the set of all reserved namespaces.
        """
        return frozenset(
            rns
            for superclass in cls.mro()
            for rns in getattr(superclass, "RDF_RESERVED_NS", ())
            )

    @classmethod
    @cache_result
    def __get_postable_in(cls):
        """
        Cache the set of all postable incoming properties.
        """
        return frozenset(chain(
                (
                    prop
                    for superclass in cls.mro()
                    for prop in getattr(superclass, "RDF_POSTABLE_IN", ())
                    ),
                (
                    prop
                    for superclass in cls.mro()
                    for prop in getattr(superclass, "RDF_PUTABLE_IN", ())
                    ),
                ))

    @classmethod
    @cache_result
    def __get_postable_out(cls):
        """
        Cache the set of all postable outgoing properties.
        """
        return frozenset(chain(
                (
                    prop
                    for superclass in cls.mro()
                    for prop in getattr(superclass, "RDF_POSTABLE_OUT", ())
                    ),
                (
                    prop
                    for superclass in cls.mro()
                    for prop in getattr(superclass, "RDF_PUTABLE_OUT", ())
                    ),
                ))

    @classmethod
    @cache_result
    def __get_postable_types(cls):
        """
        Cache the set of all postable types.
        """
        return frozenset(chain(
                [cls.RDF_MAIN_TYPE],
                (
                    typ
                    for superclass in cls.mro()
                    for typ in getattr(superclass, "RDF_POSTABLE_TYPES", ())
                    ),
                (
                    typ
                    for superclass in cls.mro()
                    for typ in getattr(superclass, "RDF_PUTABLE_TYPES", ())
                    ),
                ))

    @classmethod
    @cache_result
    def __get_puttable_in(cls):
        """
        Cache the set of all puttable incoming properties.
        """
        return frozenset(
            prop
            for superclass in cls.mro()
            for prop in getattr(superclass, "RDF_PUTABLE_IN", ())
            )

    @classmethod
    @cache_result
    def __get_puttable_out(cls):
        """
        Cache the set of all puttable outgoing properties.
        """
        return frozenset(
            prop
            for superclass in cls.mro()
            for prop in getattr(superclass, "RDF_PUTABLE_OUT", ())
            )

    @classmethod
    @cache_result
    def __get_puttable_types(cls):
        """
        Cache the set of all puttable types.
        """
        return frozenset(
            typ
            for superclass in cls.mro()
            for typ in getattr(superclass, "RDF_PUTABLE_TYPES", ())
            )

    @classmethod
    def __check_triples(cls, triples, operation, uri):
        """Check `triples` respect the reserved namespace constraints.

        :param triples:    an iterable of triples to be changed (created,
                           removed or added)
        :param operation:  "POST" or "PUT"
        :param uri:        the URI of the resource being created (POST) or
                           updated (PUT)

        :return: None if the triples are OK, or an error message.
        """

        if operation == "POST":
            types = cls.__get_postable_types()
            in_ = cls.__get_postable_in()
            out = cls.__get_postable_out()
        else:
            assert operation == "PUT"
            types = cls.__get_puttable_types()
            in_ = cls.__get_puttable_in()
            out = cls.__get_puttable_out()

        reserved = cls.__get_reserved_namespaces()
        def is_reserved(a_uri):
            "determine if uri is reserved"
            for i in reserved:
                if a_uri.startswith(i):
                    return True
            return False

        errors = []

        for s, p, o in triples:
            if s == uri:
                if p == _RDF_TYPE:
                    if is_reserved(o) and o not in types:
                        errors.append("Can not %s type <%s> for <%s>"
                                      % (operation, o, uri))
                elif is_reserved(p) and p not in out:
                    errors.append( "Can not %s property <%s> of <%s>"
                                   % (operation, p, uri))
            elif o == uri:
                if is_reserved(p) and p not in in_:
                    errors.append("Can not %s property <%s> to <%s>"
                                  % (operation, p, uri))

        if errors:
            return "\n".join(errors)
        else:
            return None


class WithCardinalityMixin(Resource):
    """
    I add cardinality constrains on some properties.

    I provide means to express cardinality constraints on some predicate, used
    as incoming and/or outgoing properties, and override `check_new_graph` to
    enforce those constraints.

    Cardinality constraints are listed in the following class variables,
    expressed as triples of the form (predicate_uri, min_cardinality,
    max_cardinality), respectively for incoming and outgoing
    properties. ``None`` can be used for min_cardinality or max_cardinality to
    mean "no constraint".

    * `RDF_CARDINALITY_IN`
    * `RDF_CARDINALITY_OUT`

    NB: the values of the class variables are *not* supposed to change over
    time; if they do, the change may not be effective.
    """

    @classmethod
    def check_new_graph(cls, uri, new_graph,
                        resource=None, added=None, removed=None):
        """I overrides :meth:`rdfrest.resource.Resource.check_new_graph` to
        check the cardinality constraints.
        """
        errors = []

        other_errors = super(WithCardinalityMixin, cls).check_new_graph(
            uri, new_graph, resource, added, removed)
        if other_errors is not None:
            errors.append(other_errors)

        new_graph_subjects = new_graph.subjects
        for p, minc, maxc in cls.__get_cardinality_in():
            nbp = len(list(new_graph_subjects(p, uri)))
            if minc is not None and nbp < minc:
                errors.append("Property <%s> to <%s> should have at least %s "
                              "subjects; it only has %s"
                              % (p, uri, minc, nbp))
            if maxc is not None and nbp > maxc:
                errors.append("Property <%s> to <%s> should have at most "
                              "%s subjects; it has %s"
                              % (p, uri, minc, nbp))

        new_graph_objects = new_graph.objects
        for p, minc, maxc in cls.__get_cardinality_out():
            nbp = len(list(new_graph_objects(uri, p)))
            if minc is not None and nbp < minc:
                errors.append("Property <%s> of <%s> should have at least "
                              "%s objects; it only has %s"
                              % (p, uri, minc, nbp))
            if maxc is not None and nbp > maxc:
                errors.append("Property <%s> of <%s> should have at most "
                              "%s objects; it has %s"
                              % (p, uri, minc, nbp))
        if errors:
            return "\n".join(errors)
        else:
            return None

    #
    # private method
    #
    @classmethod
    @cache_result
    def __get_cardinality_in(cls):
        """
        Cache the set of cardinality constraints for incoming properties.
        """
        return frozenset(
            constraint
            for superclass in cls.mro()
            for constraint in getattr(superclass, "RDF_CARDINALITY_IN", ())
            )

    @classmethod
    @cache_result
    def __get_cardinality_out(cls):
        """
        Cache the set of cardinality constraints for outgoing properties.
        """
        return frozenset(
            constraint
            for superclass in cls.mro()
            for constraint in getattr(superclass, "RDF_CARDINALITY_OUT", ())
            )


def _join_errors(errors, new_error):
    """I return the joint error message.

    :param errors:    error message, can be None
    :param new_error: error message, can be None

    :retun: None if no error, else a message
    """
    if errors is None:
        return new_error
    elif new_error is None:
        return errors
    else:
        return "%s\n%s" % (errors, new_error)

_ETAG = RDFREST.etag
_LAST_MODIFIED = RDFREST.lastModified
_RDF_TYPE = RDF.type
