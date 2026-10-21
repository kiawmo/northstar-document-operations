/* Diagnostic-only LD_PRELOAD instrumentation, never installed in runtime images.
 * Record parser calls and fixed model/image file paths, then call the real ABI.
 * Standard Linux x86_64/glibc declarations avoid a development dependency in
 * the application image. Compile with cc -shared -fPIC -o audit.so this.c -ldl.
 */
extern void *dlsym(void *, const char *);
extern char *getenv(const char *);
extern int open(const char *, int, ...);
extern long write(int, const void *, unsigned long);
extern int close(int);
extern int getpid(void);
extern int snprintf(char *, unsigned long, const char *, ...);
typedef int (*read_cb)(void *, char *, int);
typedef int (*close_cb)(void *);

static void record(const char *name, const char *path) {
  const char *destination = getenv("DOCUMENT_XML_CALL_LOG");
  if (!destination) return;
  char buffer[1024];
  int length = snprintf(buffer, sizeof(buffer), "%d %s %s\n", getpid(), name, path ? path : "");
  if (length < 0 || length >= (int)sizeof(buffer)) return;
  int fd = open(destination, 1 | 64 | 1024, 0600); /* WRONLY | CREAT | APPEND */
  if (fd >= 0) { write(fd, buffer, (unsigned long)length); close(fd); }
}

void *xmlReaderForIO(read_cb read, close_cb close, void *context, const char *url, const char *encoding, int options) {
  record("xmlReaderForIO", url);
  return ((void *(*)(read_cb,close_cb,void *,const char *,const char *,int))dlsym((void *)-1,"xmlReaderForIO"))(read,close,context,url,encoding,options);
}
void *xmlReadMemory(const char *buffer, int length, const char *url, const char *encoding, int options) {
  record("xmlReadMemory", url);
  return ((void *(*)(const char *,int,const char *,const char *,int))dlsym((void *)-1,"xmlReadMemory"))(buffer,length,url,encoding,options);
}
void *xmlReadFile(const char *url, const char *encoding, int options) {
  record("xmlReadFile", url);
  return ((void *(*)(const char *,const char *,int))dlsym((void *)-1,"xmlReadFile"))(url,encoding,options);
}
void *xmlReadIO(read_cb read, close_cb close, void *context, const char *url, const char *encoding, int options) {
  record("xmlReadIO", url);
  return ((void *(*)(read_cb,close_cb,void *,const char *,const char *,int))dlsym((void *)-1,"xmlReadIO"))(read,close,context,url,encoding,options);
}
void *xmlCtxtReadMemory(void *context, const char *buffer, int length, const char *url, const char *encoding, int options) {
  record("xmlCtxtReadMemory", url);
  return ((void *(*)(void *,const char *,int,const char *,const char *,int))dlsym((void *)-1,"xmlCtxtReadMemory"))(context,buffer,length,url,encoding,options);
}
void *xmlParseMemory(const char *buffer, int length) {
  record("xmlParseMemory", 0);
  return ((void *(*)(const char *,int))dlsym((void *)-1,"xmlParseMemory"))(buffer,length);
}
void *xmlParseFile(const char *url) {
  record("xmlParseFile", url);
  return ((void *(*)(const char *))dlsym((void *)-1,"xmlParseFile"))(url);
}
void *xmlCreatePushParserCtxt(void *sax, void *user, const char *chunk, int size, const char *filename) {
  record("xmlCreatePushParserCtxt", filename);
  return ((void *(*)(void *,void *,const char *,int,const char *))dlsym((void *)-1,"xmlCreatePushParserCtxt"))(sax,user,chunk,size,filename);
}
int xmlParseChunk(void *context, const char *chunk, int size, int terminate) {
  record("xmlParseChunk", 0);
  return ((int (*)(void *,const char *,int,int))dlsym((void *)-1,"xmlParseChunk"))(context,chunk,size,terminate);
}
void xmlParseInternalSubset(void *context) {
  record("xmlParseInternalSubset", 0);
  ((void (*)(void *))dlsym((void *)-1,"xmlParseInternalSubset"))(context);
}
int archive_read_open_filename(void *archive, const char *filename, unsigned long block_size) {
  record("archive_read_open_filename", filename);
  return ((int (*)(void *,const char *,unsigned long))dlsym((void *)-1,"archive_read_open_filename"))(archive,filename,block_size);
}
void *pixRead(const char *filename) {
  record("pixRead", filename);
  return ((void *(*)(const char *))dlsym((void *)-1,"pixRead"))(filename);
}
void *g_dbus_node_info_new_for_xml(const char *xml, void **error) {
  record("g_dbus_node_info_new_for_xml", 0);
  return ((void *(*)(const char *,void **))dlsym((void *)-1,"g_dbus_node_info_new_for_xml"))(xml,error);
}
