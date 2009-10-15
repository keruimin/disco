from disco import func
from disco.core import result_iterator, Params

def module(package):
    def module(object):
        import sys
        sys.modules['%s.%s' % (package, object.__name__)] = object
        return object
    return module

class DiscodexJob(object):
    map_input_stream     = staticmethod(func.map_input_stream)
    map_reader           = staticmethod(func.map_line_reader)
    map_writer           = staticmethod(func.netstr_writer)
    params               = Params()
    partition            = staticmethod(func.default_partition)
    profile              = False
    reduce               = None
    reduce_reader        = staticmethod(func.netstr_reader)
    reduce_writer        = staticmethod(func.netstr_writer)
    reduce_output_stream = staticmethod(func.reduce_output_stream)
    result_reader        = staticmethod(func.netstr_reader)
    required_modules     = []
    scheduler            = {}
    sort                 = False
    nr_reduces           = 1

    @staticmethod
    def map(*args, **kwargs):
        raise NotImplementedError

    @property
    def name(self):
        return self._job.name

    @property
    def results(self):
        return result_iterator(self._job.wait(), reader=self.result_reader)

    def run(self, disco_master, disco_prefix):
        jobargs = {'name':             disco_prefix,
                   'input':            self.input,
                   'map':              self.map,
                   'map_input_stream': self.map_input_stream,
                   'map_reader':       self.map_reader,
                   'map_writer':       self.map_writer,
                   'params':           self.params,
                   'partition':        self.partition,
                   'profile':          self.profile,
                   'required_modules': self.required_modules,
                   'scheduler':        self.scheduler,
                   'sort':             self.sort}

        if self.reduce:
            jobargs.update({'reduce':        self.reduce,
                            'reduce_reader': self.reduce_reader,
                            'reduce_writer': self.reduce_writer,
                            'reduce_output_stream': self.reduce_output_stream,
                            'nr_reduces':    self.nr_reduces})

        self._job = disco_master.new_job(**jobargs)
        return self

class Indexer(DiscodexJob):
    def __init__(self, dataset):
        self.input      = dataset.input
        self.map_reader = dataset.parser
        self.map        = dataset.demuxer
        self.partition  = dataset.balancer
        self.profile    = dataset.profile
        self.nr_reduces = dataset.nr_ichunks
        self.sort       = dataset.sort
        self.params     = Params(n=0)

        if dataset.k_viter:
            from discodex.mapreduce import demuxers
            self.sort = False
            self.map  = demuxers.iterdemux

    @staticmethod
    def reduce(iterator, out, params):
        # there should be a discodb writer of some sort
        from discodb import DiscoDB, kvgroup
        DiscoDB(kvgroup(iterator)).dump(out.fd)

    def reduce_output_stream(stream, partition, url, params):
        return stream, 'discodb:%s' % url.split(':', 1)[1]
    reduce_output_stream = [func.reduce_output_stream, reduce_output_stream]

class DiscoDBIterator(DiscodexJob):
    scheduler = {'force_local': True}
    method    = 'keys'

    def __init__(self, ichunks):
        self.ichunks = ichunks

    @property
    def input(self):
        return ['%s/%s/' % (ichunk, self.method) for ichunk in self.ichunks]

    @staticmethod
    def map_reader(fd, size, fname):
        if hasattr(fd, '__iter__'):
            return fd
        return func.map_line_reader(fd, size, fname)

    @staticmethod
    def map(entry, params):
        return [(None, entry)]

class KeyIterator(DiscoDBIterator):
    pass

class ValuesIterator(DiscoDBIterator):
    method = 'values'

class Queryer(DiscoDBIterator):
    method = 'query'

    def __init__(self, ichunks, query):
        super(Queryer, self).__init__(ichunks)
        self.params = Params(discodb_query=query.urlformat())

class Record(object):
    __slots__ = ('fields', 'fieldnames')

    def __init__(self, *fields, **namedfields):
        for name in namedfields:
            if name in self.__slots__:
                raise ValueError('Use of reserved fieldname: %r' % name)
        self.fields = (list(fields) + namedfields.values())
        self.fieldnames = len(fields) * [None] + namedfields.keys()

    def __getattr__(self, attr):
        for n, name in enumerate(self.fieldnames):
            if attr == name:
                return self[n]
        raise AttributeError('%r has no attribute %r' % (self, attr))

    def __getitem__(self, index):
        return self.fields[index]

    def __iter__(self):
        from itertools import izip
        return izip(self.fieldnames, self.fields)

    def __repr__(self):
        return 'Record(%s)' % ', '.join('%s=%r' % (n, f) if n else '%r' % f
                                      for f, n in zip(self.fields, self.fieldnames))


# ichunk parser == func.discodb_reader (iteritems)


# parser:  data -> records       \
#                                 | kvgenerator            \
# demuxer: record -> k, v ...    /                          |
#                                                           | indexer
#                                                           |
# balancer: (k, ) ... -> (p, (k, )) ...   \                /
#                                          | ichunkbuilder
# ichunker: (p, (k, v) ... -> ichunks     /
