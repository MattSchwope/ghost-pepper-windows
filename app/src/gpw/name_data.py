"""Word lists for on-screen name spelling (Phase 2b). Data only.

NAME_VARIANTS: common first names, one line per name, with the spellings that sound the same.
Two names that are both listed match only when they share a line, so Sean and Shawn match but
Sean and Shane, or Dan and Don, never do. A name that is not listed (most surnames) is matched
by sound and spelling instead (names.sounds_alike). Nicknames are separate names: Bill is not
William. Add a missing spelling to its line; add a missing name as its own line.

COMMON_WORDS: capitalized words that are almost never a name on screen (sentence starts, UI
labels, days and months), and words that are names only sometimes (Will, Mark, Grace). The
second kind are still read from the screen inside a full name ("Mark Jones"), and are still
fixed in the middle of a sentence, but never at the start of one, so "Mark the date" stays.
"""

from __future__ import annotations

NAME_VARIANTS = """
Aaron Aron Arron
Abby Abbey Abbie
Abigail Abigale Abigayle
Adam Addam
Adrian Adrien
Adrienne Adriene
Aidan Aiden Aden Aydan Ayden
Aileen Eileen Ilene Aline
Alan Allan Allen Alen Allyn
Alana Alanna Alannah
Alex Alec Alek Alexx
Alexa Alexia
Alexander Alexandar Alexzander
Alexandra Alexandria
Alicia Alisha Alysha Alesha
Alison Allison Alyson Allyson
Alyssa Alissa Alisa Alysa Elissa
Amanda
Amber
Amelia Amilia
Amy Aimee Amie Ami
Andrea Andria
Andrew
Angela
Angelina
Ann Anne Anna Ana
Annette Annett
Antonio
Anthony Antony
April Aprille
Ariana Arianna Ariyana
Ashley Ashlee Ashleigh Ashly Ashlie
Ashton Ashten
Aubrey Aubree Aubrie
Audrey Audrie
Austin Austen Auston
Autumn
Bailey Baylee Bailee Baley
Barbara Barbra
Ben
Benjamin Benjamen
Beth
Bethany Bethanie
Betty Bettie
Beverly Beverley
Bill
Blake
Bobby Bobbie
Brad
Bradley Bradly
Brandon Branden Brandan
Brenda
Brendan Brenden Brendon
Brent
Brett Bret
Brian Bryan Brien Bryon
Briana Brianna Breanna Brianne Bryanna
Bridget Bridgette Brigitte
Britney Brittany Brittney Britany Brittani
Brooke Brook
Bruce
Bryce Brice
Caitlin Kaitlyn Katelyn Kaitlin Caitlyn Katelin Kaitlynn Catelyn
Caleb Kaleb
Cameron Kameron Camron
Camila Camilla
Candace Candice Candis
Cara Kara Carah
Carl Karl
Carla Karla
Carly Carley Carlie Karly Karlie
Carmen
Carol Carole Carrol Karol
Caroline Carolyn Karolyn Carolin Carolynn
Carrie Kari Cari Carey Kerry Keri
Casey Kasey Kacey Kacy Kaci
Cassandra Kassandra
Catherine Katherine Kathryn Katharine Kathrine Cathryn Catharine Kathrynn
Cathy Kathy Kathie Cathie
Cecilia Cecelia
Chad
Charles
Charlotte
Chelsea Chelsey Chelsie Chelsi
Cheryl Sheryl Sherrill
Cheyenne Shayenne
Chloe Khloe Cloe
Chris Kris
Christian Kristian Cristian Christion
Christina Kristina Cristina Christeena
Christine Kristine Kristin Kristen Christen Kirsten
Christopher Kristopher Cristopher Christofer
Cindy Cyndi Cyndy
Claire Clare Clair
Clara Klara
Clayton Clayten
Cody Kody Codey
Colin Collin Colen
Connor Conner Conor Konnor
Corey Cory Korey Kory Cori
Courtney Kourtney Cortney
Craig Kraig
Crystal Krystal Cristal Chrystal
Curtis Kurtis
Cynthia Cinthia
Daisy Daisey
Dakota
Dale
Dan
Dana Dayna
Daniel Daniël
Danielle Daniela Daniella Danyelle
Danny Dannie
Darren Darin Darrin Daren
David
Dawn
Dean Deane
Deanna Diana Dianna Deana
Debbie Debby Debi Debbi
Deborah Debra Debora Debrah
Dennis Denis
Derek Derrick Derrik Deric Derik
Destiny Destinee
Devin Devon Devan Devyn
Diane Dianne Dyan Dian
Dominic Dominick Dominik
Don
Donald
Donna Dona
Doug
Douglas Douglass
Dustin Dusten
Dylan Dillon Dillan Dilan
Edward
Elaine Elayne
Eleanor Elinor Elenor Eleanore
Elena Alena
Elijah Elija
Elizabeth Elisabeth Elizebeth
Ella Ela
Ellen Ellyn Elen
Emily Emilee Emilie Emely
Emma Ema
Eric Erik Erick Eryk
Erica Erika Ericka
Erin Eryn Erinn
Ethan Ethen
Eva Ava Iva
Evan Evin
Faith
Felicia Phylicia Felisha
Frances
Francis
Frank
Gabriel Gabriell
Gabrielle Gabriella Gabriela
Gary Garry
Gail Gayle Gale
Gene
Geoffrey Jeffrey Jeffery Jeffry
George
Gerald Jerald Gerrald
Gina Geena
Grace
Graham Graeme Grahame
Greg Gregg
Gregory Gregorie
Hailey Haley Hayley Haylee Hailee Haleigh
Hannah Hanna Hana
Heather
Heidi Heide Heidy
Helen Hellen Helene
Holly Hollie Holley
Hunter
Ian Iain
Isaac Issac Isaak
Isabel Isabelle Isabella Izabella Isobel Isabell
Jack
Jackson Jaxon Jaxson Jakson
Jacob Jakob
Jacqueline Jaqueline Jacquelyn Jacquelin
Jade Jayde
Jaime Jamie Jayme Jaimie
James
Jan
Jane Jayne Jaine
Janet Janette Jannette
Janice Janis
Jared Jarrod Jarod Jarred
Jasmine Jasmin Jazmin Jazmine Yasmin Yasmine
Jason Jayson Jasen
Jean Gene Jeanne
Jeff Geoff Jef
Jen
Jenna Jena
Jennifer Jenifer Jenefer
Jenny Jennie Jeni
Jeremy Jeremie
Jerry Gerry Jerri
Jesse Jessie Jessy
Jessica Jesica Jessika
Jill Jil
Jillian Gillian Jilian
Joan Joanne Joann Jo-Ann
Jocelyn Joselyn Jocelynn Josslyn
Jodi Jody Jodie
Joe Jo
John Jon Jonn
Johnny Johnnie Jonny
Jonathan Jonathon Johnathan Johnathon
Jordan Jordyn Jorden
Joseph Josef
Josh
Joshua
Joy Joi
Joyce
Judy Judi Judie
Julia
Julian Julien
Julie Julee
Justin Justen Juston
Karen Karin Caren Karyn Keren
Kate Cate
Katie Katy Katey Kati Caty
Kayla Kaila Kaela
Kaylee Kayleigh Kailee Kaley Caley Kaylie
Keith
Kelly Kelli Kellie Kelley Keli
Kelsey Kelsie Kelsi Kelcie
Kendall Kendal
Kendra
Kenneth Kenith
Kevin Kevan
Kim Kym
Kimberly Kimberley Kimberlee
Kristy Kristi Kristie Christy Christie Christi
Kyle Kile
Kylie Kylee Kiley Kyleigh
Lacey Lacy Laci
Laura Lora
Lauren Loren Lauryn Laurin
Laurie Lori Lorie Lory
Leah Lea Lia
Lee Leigh Lea
Leslie Lesley Lesly
Lily Lilly Lillie Lili
Linda Lynda
Lindsay Lindsey Lindsy Linsey Lyndsey Lyndsay
Lisa Leesa Liza
Logan
Louis Lewis Luis
Lucas Lukas
Luke Luc
Lynn Lyn Lynne Linn
Mackenzie McKenzie Makenzie Mckenzie MacKenzie
Madeline Madeleine Madelyn Madalyn Madilyn
Madison Madisyn Maddison Madyson
Mallory Mallorie Malorie
Mandy Mandi Mandie
Marc Mark Marck
Marcus Markus Marcos Markos
Margaret Margret
Maria Mariah Marya
Marie Mari
Marissa Marisa Morissa
Martin Marten
Mary Merry Marie
Mason Mayson
Matt Mat
Matthew Mathew Matthieu
Maxwell Maxwel
Megan Meghan Meagan Meaghan Megann
Melanie Melany Melony
Melissa Melisa Mellisa
Meredith Meridith
Michael Micheal Mikael
Michaela Mikayla Makayla Micaela Mikaela Michela
Michele Michelle Mishelle Michell
Mike
Mitchell Mitchel
Molly Mollie
Monica Monika
Morgan Morgen
Muhammad Mohammed Mohammad Muhammed Mohamed
Nancy Nanci
Natalie Nathalie Natalee Nataly
Nate
Nathan Nathen
Nathaniel Nathanael
Neil Neal Niall
Nicholas Nicolas Nickolas Niklas
Nick Nik Nic
Nicole Nichole Nicol Nicolle
Noah
Noel Noelle Nowell
Olivia
Owen Owin
Paige Page
Pam
Pamela Pamella
Patricia
Patrick Patric
Paul Paule
Paula
Peter Pieter
Philip Phillip Filip Phillipe
Phyllis Phillis
Rachel Rachael Rachelle Raquel
Ralph
Randall Randal
Randy Randi
Raymond Raymund
Rebecca Rebekah Rebeca Rebekka
Reid Reed
Renee Renae Renée
Rhonda Ronda
Richard
Ricky Rickie Rikki
Riley Reilly Rylee Ryleigh
Rob
Robert
Robin Robyn
Rodney Rodny
Ronald
Rose
Ross
Ruth
Ryan Rian Ryann
Sabrina Sabrena
Sam
Samantha
Samuel
Sandra Sondra
Sandy Sandi Sandie
Sara Sarah Sera
Scott Scot
Sean Shawn Shaun Shon
Seth
Shane Shayne Shain
Shannon Shanon Shannen
Sharon Sharron Sharyn
Shawna Shauna Seana
Shelby Shelbie
Shelly Shelley Shellie
Sheila Shelia Sheilah
Sherry Sheri Sherri Sherrie Cherie
Sidney Sydney Sydnee Sydni
Sierra Ciera Cierra
Sofia Sophia
Stacy Stacey Staci Stacie
Stephanie Stefanie Stephany Stefani
Stephen Steven Stephan Stefan Stevan
Steve Stev
Stuart Stewart
Summer
Susan Suzanne Susanne Suzan
Suzie Susie Suzy Susy
Tamara Tamra
Tammy Tammie Tami
Tanya Tania Tonya
Tara Tarah Terra
Taylor Tayler Tailor
Teresa Theresa Teressa Tereza
Terry Terri Teri Terrie
Thomas Tomas
Tiffany Tiffani Tiffanie
Timothy Timmothy
Todd Tod
Tom
Tony Toni Tonie
Tracy Tracey Traci Tracie
Travis Travys
Trevor Trever
Troy Troi
Tyler Tylor
Valerie Valery Valarie
Vanessa Venessa
Veronica Veronika
Vicki Vicky Vickie Vikki
Victoria Viktoria
Vincent Vinsent
Wade
Walter Walt
Wayne Wain
Wendy Wendi
Will Wil
Whitney Whitny
William
Wyatt Wyat
Zach Zack Zak Zac
Zachary Zackary Zachery Zakary
Zoe Zoey Zoie Zoë
"""

COMMON_WORDS = frozenset("""
a about above account accept across action actions active activity add added address admin after afternoon again
against agenda ago ai al alert alerts all allow almost alone along already also always am among an and another answer
any anyone anything app apply appointment approve apps april are area around art as ask assign at attach attached
attachment attachments august auto available away back bank based be because been before being below best better
between bill billing block blue body bold book both bottom box bring browse busy but button buy by calendar call
calls can cancel cannot card case categories category cc center change changes channel channels chat chats check
choose clear click close closed code collapse color comment comments company complete confirm connect contact
contacts content continue copy cost could create created current cut daily dark dashboard data date day days dear
december decline default delete deleted delivered design details did direct directory disable discard dismiss display
do document documents does done down download draft drafts due during each easy edit editor email emails empty enable
end enter entire error even evening event events every everyone everything example expand export extensions favorite
favorites feature features february feed file files filter find first flag flagged focused folder folders follow
following font for format forward free friday friends from full general get give go good got grace great group groups
had has have he header hello help her here hey hi hide high his history hold home hope how however i if image images
import important in inbox include info information insert into invite is it item items its january join joy july
june just keep key know label labels language last later latest learn leave left let like link links list live load
loading local location lock log login logout long look low mail main make manage many march mark me media meeting
meetings members menu message messages microsoft might mobile monday month more morning most move much my name
names navigation need network never new news next no none normal not note notes nothing notification notifications
november now number of off office ok okay on once one online only open options or order other others our out
outbox over overview own page pages paste people per period phone pin pinned plan please plus post posts preview
previous print priority privacy private profile project projects public quick read ready really reply report reports
request required reset resources results review right rose rule rules run save saved schedule search second section
security see select send sent september server service set settings share shared sharing sheet show sign since site
size skip so some someone something soon sort source spam start started status still stop subject submit summary
sunday support sure switch sync system tab table tag tags task tasks team teams terms text than thank thanks that
the their them then there these they thing things think this those thread through thursday time to today together
tomorrow too tool tools top total trash try tuesday type under undo unread until up update updates upgrade upload
us use used user users using version very via video view views visit want was watch we wednesday week weekly welcome
well were what when where which while who why will window with without word work workspace would write year yes
yesterday yet you your yours
outlook gmail google chrome edge firefox safari slack linkedin zoom webex excel powerpoint onenote onedrive sharepoint
windows teams word copilot notepad
senior junior manager management program project product director engineer engineering recruiter recruiting lead
principal specialist president vice chief officer consultant analyst associate coordinator executive founder partner
emoji keyboard gif premium business conversation conversations
""".split())


def variant_groups() -> dict[str, frozenset[int]]:
    """Casefolded spelling -> the NAME_VARIANTS lines it is on. A few spellings sit on two (Lea: Leah and Lee)."""
    groups: dict[str, set[int]] = {}
    for number, line in enumerate(NAME_VARIANTS.strip().splitlines()):
        for name in line.split():
            groups.setdefault(name.casefold(), set()).add(number)
    return {name: frozenset(lines) for name, lines in groups.items()}


# Long first names whose usual short form has competing spellings: the long form on screen says
# which one this person uses (Jonathan goes by Jon, Johnathan by John). Used only for names near
# the cursor, and only to pick a short form's spelling: a short form is never expanded to the long
# name, and nicknames that are other names (Bill and William) are not here.
SHORT_FORMS = {
    "jonathan": "Jon", "jonathon": "Jon", "johnathan": "John", "johnathon": "John",
    "zachary": "Zach", "zachery": "Zach", "zachariah": "Zach", "zackary": "Zack", "zackery": "Zack",
    "christopher": "Chris", "kristopher": "Kris",
    "jeffrey": "Jeff", "jeffery": "Jeff", "geoffrey": "Geoff",
    "matthew": "Matt",
}
